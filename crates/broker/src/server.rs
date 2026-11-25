//! The mTLS HTTP server: `GET /v1/token?host=<host>`.
use std::{convert::Infallible, io, sync::Arc};

use http_body_util::Full;
use hyper::{
    body::{Bytes, Incoming},
    service::service_fn,
    Method, Request, Response, StatusCode,
};
use hyper_util::rt::TokioIo;
use rustls::{
    pki_types::{pem::PemObject, CertificateDer, PrivateKeyDer},
    server::WebPkiClientVerifier,
    RootCertStore, ServerConfig,
};
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;

use serde_json::json;

use crate::{
    policy::{Grant, Policy},
    source::{GithubApi, Issued, SourceError, StaticSecrets},
};

/// PEM inputs for the listener. The client CA is the only trust root for runners.
pub struct TlsMaterial {
    pub server_cert_pem: String,
    pub server_key_pem: String,
    pub client_ca_pem: String,
}

#[derive(Debug, thiserror::Error)]
pub enum ServeError {
    #[error("tls material is not usable: {0}")]
    Tls(String),
    #[error("listener failed: {0}")]
    Io(#[from] io::Error),
}

fn acceptor(tls: &TlsMaterial) -> Result<TlsAcceptor, ServeError> {
    let bad = |e: &dyn std::fmt::Display| ServeError::Tls(e.to_string());
    let mut roots = RootCertStore::empty();
    for ca in CertificateDer::pem_slice_iter(tls.client_ca_pem.as_bytes()) {
        roots.add(ca.map_err(|e| bad(&e))?).map_err(|e| bad(&e))?;
    }
    let verifier = WebPkiClientVerifier::builder(Arc::new(roots))
        .build()
        .map_err(|e| bad(&e))?;
    let chain = CertificateDer::pem_slice_iter(tls.server_cert_pem.as_bytes())
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| bad(&e))?;
    let key = PrivateKeyDer::from_pem_slice(tls.server_key_pem.as_bytes()).map_err(|e| bad(&e))?;
    let config =
        ServerConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()
            .map_err(|e| bad(&e))?
            .with_client_cert_verifier(verifier)
            .with_single_cert(chain, key)
            .map_err(|e| bad(&e))?;
    Ok(TlsAcceptor::from(Arc::new(config)))
}

struct State<G> {
    policy: Policy,
    github: G,
    secrets: StaticSecrets,
}

fn reply(status: StatusCode, body: serde_json::Value) -> Response<Full<Bytes>> {
    Response::builder()
        .status(status)
        .header("content-type", "application/json")
        .body(Full::new(Bytes::from(body.to_string())))
        .expect("static response is valid")
}

/// The first certificate's subject common name, which is the runner identity.
fn subject_of(cert: &CertificateDer<'_>) -> Option<String> {
    let (_, parsed) = x509_parser::parse_x509_certificate(cert).ok()?;
    let cn = parsed.subject().iter_common_name().next()?;
    cn.as_str().ok().map(str::to_owned)
}

fn host_param(query: Option<&str>) -> Option<&str> {
    query?
        .split('&')
        .find_map(|pair| pair.strip_prefix("host="))
        .filter(|h| !h.is_empty())
}

async fn handle<G: GithubApi>(
    state: &State<G>,
    subject: &str,
    req: &Request<Incoming>,
) -> Response<Full<Bytes>> {
    if req.method() != Method::GET || req.uri().path() != "/v1/token" {
        return reply(StatusCode::NOT_FOUND, json!({"error": "not-found"}));
    }
    let Some(host) = host_param(req.uri().query()) else {
        return reply(StatusCode::BAD_REQUEST, json!({"error": "host-required"}));
    };
    let grant = match state.policy.decide(subject, host) {
        Ok(grant) => grant,
        Err(denied) => {
            tracing::info!(subject, host, reason = denied.code(), "denied");
            return reply(StatusCode::FORBIDDEN, json!({"error": denied.code()}));
        }
    };
    let issued = match grant {
        Grant::Github { repos, write } => state.github.installation_token(repos, *write).await,
        Grant::Static { name } => state
            .secrets
            .get(name)
            .map(|token| Issued {
                token: token.to_owned(),
                expires_at: None,
            })
            .ok_or_else(|| SourceError::Missing(name.clone())),
    };
    match issued {
        Ok(issued) => {
            tracing::info!(subject, host, "issued");
            reply(
                StatusCode::OK,
                json!({"token": issued.token, "expires_at": issued.expires_at}),
            )
        }
        Err(error) => {
            tracing::warn!(subject, host, %error, "mint failed");
            reply(StatusCode::BAD_GATEWAY, json!({"error": "mint-failed"}))
        }
    }
}

/// Serve until the listener fails. Every connection must present a client certificate
/// that chains to the client CA and is inside its validity window.
pub async fn serve<G: GithubApi>(
    listener: TcpListener,
    tls: &TlsMaterial,
    policy: Policy,
    github: G,
    secrets: StaticSecrets,
) -> Result<(), ServeError> {
    let acceptor = acceptor(tls)?;
    let state = Arc::new(State {
        policy,
        github,
        secrets,
    });
    loop {
        let (tcp, _) = listener.accept().await?;
        let acceptor = acceptor.clone();
        let state = Arc::clone(&state);
        tokio::spawn(async move {
            let Ok(stream) = acceptor.accept(tcp).await else {
                return;
            };
            let subject = stream
                .get_ref()
                .1
                .peer_certificates()
                .and_then(|chain| chain.first())
                .and_then(subject_of);
            let Some(subject) = subject else { return };
            let service = service_fn(|req| {
                let state = Arc::clone(&state);
                let subject = subject.clone();
                async move { Ok::<_, Infallible>(handle(&state, &subject, &req).await) }
            });
            let _ = hyper::server::conn::http1::Builder::new()
                .serve_connection(TokioIo::new(stream), service)
                .await;
        });
    }
}
