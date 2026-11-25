//! The mTLS HTTP server: `GET /v1/token?host=<host>`.
use std::{io, sync::Arc};

use http_body_util::Full;
use hyper::{body::Bytes, service::service_fn, Response, StatusCode};
use hyper_util::rt::TokioIo;
use rustls::{
    pki_types::{pem::PemObject, CertificateDer, PrivateKeyDer},
    server::WebPkiClientVerifier,
    RootCertStore, ServerConfig,
};
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;

use crate::{
    policy::Policy,
    source::{GithubApi, StaticSecrets},
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
    let verifier = WebPkiClientVerifier::builder(Arc::new(roots)).build().map_err(|e| bad(&e))?;
    let chain = CertificateDer::pem_slice_iter(tls.server_cert_pem.as_bytes())
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| bad(&e))?;
    let key = PrivateKeyDer::from_pem_slice(tls.server_key_pem.as_bytes()).map_err(|e| bad(&e))?;
    let config = ServerConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions()
        .map_err(|e| bad(&e))?
        .with_client_cert_verifier(verifier)
        .with_single_cert(chain, key)
        .map_err(|e| bad(&e))?;
    Ok(TlsAcceptor::from(Arc::new(config)))
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
    let _ = (&policy, &github, &secrets);
    loop {
        let (tcp, _) = listener.accept().await?;
        let acceptor = acceptor.clone();
        tokio::spawn(async move {
            let Ok(stream) = acceptor.accept(tcp).await else { return };
            let service = service_fn(|_req| async {
                Ok::<_, std::convert::Infallible>(
                    Response::builder()
                        .status(StatusCode::NOT_IMPLEMENTED)
                        .body(Full::new(Bytes::from_static(b"{}")))
                        .expect("static response is valid"),
                )
            });
            let _ = hyper::server::conn::http1::Builder::new()
                .serve_connection(TokioIo::new(stream), service)
                .await;
        });
    }
}
