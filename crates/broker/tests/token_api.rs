//! Acceptance: a runner with a valid client certificate gets a host-scoped credential;
//! every other caller is refused. Real TLS, a throwaway CA, a fake GitHub.
use std::{
    collections::HashMap,
    future::Future,
    io,
    sync::{Arc, Mutex},
};

use credential_broker::{
    policy::Policy,
    server::{serve, TlsMaterial},
    source::{GithubApi, Issued, SourceError, StaticSecrets},
};
use rcgen::{
    BasicConstraints, Certificate, CertificateParams, DistinguishedName, DnType,
    ExtendedKeyUsagePurpose, IsCa, KeyPair, KeyUsagePurpose,
};
use rustls::{
    pki_types::{pem::PemObject, CertificateDer, PrivateKeyDer, ServerName},
    ClientConfig, RootCertStore,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpStream},
};
use tokio_rustls::TlsConnector;

const POLICY: &str = r#"{"rules":[
  {"subject":"runner-1","host":"api.github.com","source":"github","repos":["sandy"],"write":false},
  {"subject":"runner-1","host":"api.anthropic.com","source":"static","name":"anthropic"}
]}"#;

struct Ca {
    cert: Certificate,
    key: KeyPair,
}

impl Ca {
    fn new(name: &str) -> Self {
        let mut params = CertificateParams::new(Vec::<String>::new()).expect("params");
        params.distinguished_name = DistinguishedName::new();
        params.distinguished_name.push(DnType::CommonName, name);
        params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        params.key_usages = vec![KeyUsagePurpose::KeyCertSign];
        let key = KeyPair::generate().expect("key");
        let cert = params.self_signed(&key).expect("self sign");
        Self { cert, key }
    }

    /// A leaf signed by this CA. `expired` backdates the validity window.
    fn leaf(
        &self,
        cn: &str,
        san: Vec<String>,
        usage: ExtendedKeyUsagePurpose,
        expired: bool,
    ) -> (String, String) {
        let mut params = CertificateParams::new(san).expect("params");
        params.distinguished_name = DistinguishedName::new();
        params.distinguished_name.push(DnType::CommonName, cn);
        params.extended_key_usages = vec![usage];
        if expired {
            params.not_before = rcgen::date_time_ymd(2019, 1, 1);
            params.not_after = rcgen::date_time_ymd(2020, 1, 1);
        }
        let key = KeyPair::generate().expect("key");
        let cert = params.signed_by(&key, &self.cert, &self.key).expect("sign");
        (cert.pem(), key.serialize_pem())
    }
}

/// Repositories and write flag of one mint request.
type Call = (Vec<String>, bool);

#[derive(Clone, Default)]
struct FakeGithub {
    calls: Arc<Mutex<Vec<Call>>>,
}

impl GithubApi for FakeGithub {
    fn installation_token(
        &self,
        repos: &[String],
        write: bool,
    ) -> impl Future<Output = Result<Issued, SourceError>> + Send {
        self.calls
            .lock()
            .expect("lock")
            .push((repos.to_vec(), write));
        async {
            Ok(Issued {
                token: "ghs_fake".to_owned(),
                expires_at: Some(4_000_000_000),
            })
        }
    }
}

struct Harness {
    addr: std::net::SocketAddr,
    runners_ca: Ca,
    server_ca_pem: String,
    github: FakeGithub,
}

async fn start() -> Harness {
    let server_ca = Ca::new("server-ca");
    let runners_ca = Ca::new("runners-ca");
    let (server_cert_pem, server_key_pem) = server_ca.leaf(
        "broker",
        vec!["localhost".to_owned()],
        ExtendedKeyUsagePurpose::ServerAuth,
        false,
    );
    let tls = TlsMaterial {
        server_cert_pem,
        server_key_pem,
        client_ca_pem: runners_ca.cert.pem(),
    };
    let github = FakeGithub::default();
    let secrets = StaticSecrets::new(HashMap::from([(
        "anthropic".to_owned(),
        "sk-ant-real".to_owned(),
    )]));
    let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
    let addr = listener.local_addr().expect("addr");
    let policy = Policy::parse(POLICY).expect("policy");
    let served = github.clone();
    tokio::spawn(async move {
        let _ = serve(listener, &tls, policy, served, secrets).await;
    });
    Harness {
        addr,
        runners_ca,
        server_ca_pem: server_ca.cert.pem(),
        github,
    }
}

/// One GET over mTLS. `client` is the (cert, key) PEM pair, or `None` for no certificate.
async fn get(
    h: &Harness,
    client: Option<(String, String)>,
    path: &str,
) -> io::Result<(u16, String)> {
    let mut roots = RootCertStore::empty();
    for ca in CertificateDer::pem_slice_iter(h.server_ca_pem.as_bytes()) {
        roots.add(ca.expect("ca pem")).expect("root");
    }
    let builder =
        ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()
            .expect("versions")
            .with_root_certificates(roots);
    let config = match client {
        Some((cert, key)) => {
            let chain = CertificateDer::pem_slice_iter(cert.as_bytes())
                .collect::<Result<Vec<_>, _>>()
                .expect("cert pem");
            let key = PrivateKeyDer::from_pem_slice(key.as_bytes()).expect("key pem");
            builder
                .with_client_auth_cert(chain, key)
                .expect("client auth")
        }
        None => builder.with_no_client_auth(),
    };
    let tcp = TcpStream::connect(h.addr).await?;
    let mut tls = TlsConnector::from(Arc::new(config))
        .connect(ServerName::try_from("localhost").expect("name"), tcp)
        .await?;
    tls.write_all(
        format!("GET {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n").as_bytes(),
    )
    .await?;
    let mut raw = Vec::new();
    tls.read_to_end(&mut raw).await?;
    let text = String::from_utf8_lossy(&raw).into_owned();
    let status = text
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "no status line"))?;
    let body = text.split("\r\n\r\n").nth(1).unwrap_or_default().to_owned();
    Ok((status, body))
}

fn runner(h: &Harness, cn: &str) -> (String, String) {
    h.runners_ca
        .leaf(cn, vec![], ExtendedKeyUsagePurpose::ClientAuth, false)
}

/// True when the call did not end in a 2xx answer: a TLS failure or an error status.
fn refused(outcome: &io::Result<(u16, String)>) -> bool {
    !matches!(outcome, Ok((200..=299, _)))
}

#[tokio::test]
async fn token_api_should_return_a_github_token_when_the_runner_is_allowed() {
    let h = start().await;
    let (status, body) = get(
        &h,
        Some(runner(&h, "runner-1")),
        "/v1/token?host=api.github.com",
    )
    .await
    .expect("answer");
    assert_eq!(status, 200, "{body}");
    assert!(body.contains("\"token\":\"ghs_fake\""), "{body}");
    assert!(body.contains("\"expires_at\":4000000000"), "{body}");
    let calls = h.github.calls.lock().expect("lock").clone();
    assert_eq!(calls, vec![(vec!["sandy".to_owned()], false)]);
}

#[tokio::test]
async fn token_api_should_return_the_held_secret_when_the_rule_is_static() {
    let h = start().await;
    let (status, body) = get(
        &h,
        Some(runner(&h, "runner-1")),
        "/v1/token?host=api.anthropic.com",
    )
    .await
    .expect("answer");
    assert_eq!(status, 200, "{body}");
    assert!(body.contains("\"token\":\"sk-ant-real\""), "{body}");
}

#[tokio::test]
async fn token_api_should_deny_when_the_host_has_no_rule_for_the_subject() {
    let h = start().await;
    let (status, body) = get(
        &h,
        Some(runner(&h, "runner-1")),
        "/v1/token?host=evil.example",
    )
    .await
    .expect("answer");
    assert_eq!(status, 403, "{body}");
    assert!(body.contains("no-rule"), "{body}");
    assert!(h.github.calls.lock().expect("lock").is_empty());
}

#[tokio::test]
async fn token_api_should_deny_when_the_subject_is_outside_the_policy() {
    let h = start().await;
    let (status, _) = get(
        &h,
        Some(runner(&h, "runner-2")),
        "/v1/token?host=api.github.com",
    )
    .await
    .expect("answer");
    assert_eq!(status, 403);
    assert!(h.github.calls.lock().expect("lock").is_empty());
}

#[tokio::test]
async fn token_api_should_reject_when_no_client_certificate_is_presented() {
    let h = start().await;
    let outcome = get(&h, None, "/v1/token?host=api.github.com").await;
    assert!(refused(&outcome), "{outcome:?}");
    assert!(h.github.calls.lock().expect("lock").is_empty());
}

#[tokio::test]
async fn token_api_should_reject_when_the_certificate_comes_from_a_foreign_ca() {
    let h = start().await;
    let foreign = Ca::new("foreign");
    let cert = foreign.leaf(
        "runner-1",
        vec![],
        ExtendedKeyUsagePurpose::ClientAuth,
        false,
    );
    let outcome = get(&h, Some(cert), "/v1/token?host=api.github.com").await;
    assert!(refused(&outcome), "{outcome:?}");
    assert!(h.github.calls.lock().expect("lock").is_empty());
}

#[tokio::test]
async fn token_api_should_reject_when_the_certificate_is_expired() {
    let h = start().await;
    let cert = h.runners_ca.leaf(
        "runner-1",
        vec![],
        ExtendedKeyUsagePurpose::ClientAuth,
        true,
    );
    let outcome = get(&h, Some(cert), "/v1/token?host=api.github.com").await;
    assert!(refused(&outcome), "{outcome:?}");
    assert!(h.github.calls.lock().expect("lock").is_empty());
}

#[tokio::test]
async fn token_api_should_answer_400_when_the_host_parameter_is_missing() {
    let h = start().await;
    let (status, _) = get(&h, Some(runner(&h, "runner-1")), "/v1/token")
        .await
        .expect("answer");
    assert_eq!(status, 400);
}
