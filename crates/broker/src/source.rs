//! Where credentials come from. GitHub minting sits behind a trait so tests and the
//! OpenBao-signed implementation plug in the same way.
use std::{collections::HashMap, future::Future};

/// A minted credential and its expiry (unix seconds).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Issued {
    pub token: String,
    pub expires_at: u64,
}

#[derive(Debug, thiserror::Error)]
pub enum SourceError {
    #[error("upstream refused or failed: {0}")]
    Upstream(String),
    #[error("no secret named {0}")]
    Missing(String),
}

/// Mints GitHub App installation tokens narrowed by repository and permission profile.
pub trait GithubApi: Send + Sync + 'static {
    fn installation_token(
        &self,
        repos: &[String],
        write: bool,
    ) -> impl Future<Output = Result<Issued, SourceError>> + Send;
}

/// Held secrets by name (for example the Anthropic key), never logged.
#[derive(Clone, Default)]
pub struct StaticSecrets(HashMap<String, String>);

impl StaticSecrets {
    pub fn new(map: HashMap<String, String>) -> Self {
        Self(map)
    }

    pub fn get(&self, name: &str) -> Option<&str> {
        self.0.get(name).map(String::as_str)
    }
}

impl std::fmt::Debug for StaticSecrets {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("StaticSecrets").finish_non_exhaustive()
    }
}
