//! Deny-by-default policy: which runner subject may get which credential for which host.
use serde::Deserialize;

/// What the broker mints when a rule matches.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(tag = "source", rename_all = "lowercase")]
pub enum Grant {
    /// A GitHub App installation token narrowed to `repos`; `write` selects the write profile.
    Github { repos: Vec<String>, write: bool },
    /// A held secret, looked up by `name`.
    Static { name: String },
}

/// One allow rule. A rule matches on exact `subject` and exact `host`.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Rule {
    pub subject: String,
    pub host: String,
    #[serde(flatten)]
    pub grant: Grant,
}

/// Why a request was refused. `code()` is the stable reason string in responses and audit.
#[derive(Debug, Clone, Copy, PartialEq, Eq, thiserror::Error)]
pub enum Denied {
    #[error("no rule matches this subject and host")]
    NoRule,
}

impl Denied {
    pub fn code(self) -> &'static str {
        match self {
            Self::NoRule => "no-rule",
        }
    }
}

/// Why a policy file was refused at load.
#[derive(Debug, thiserror::Error)]
pub enum PolicyError {
    #[error("policy is not valid: {0}")]
    Invalid(String),
    #[error("policy has no rules; refusing to start with a policy that denies everything silently")]
    Empty,
}

/// The loaded rule set.
#[derive(Debug, Clone)]
pub struct Policy {
    rules: Vec<Rule>,
}

impl Policy {
    /// Parse `{"rules":[...]}`. An empty rule list is an error.
    pub fn parse(json: &str) -> Result<Self, PolicyError> {
        let _ = json;
        Err(PolicyError::Empty)
    }

    /// The grant for `subject` asking about `host`, or why not.
    pub fn decide(&self, subject: &str, host: &str) -> Result<&Grant, Denied> {
        let _ = (subject, host, &self.rules);
        Err(Denied::NoRule)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const ONE: &str = r#"{"rules":[{"subject":"r1","host":"api.github.com","source":"github","repos":["sandy"],"write":true}]}"#;

    #[test]
    fn policy_should_allow_when_subject_and_host_match_a_rule() {
        let policy = Policy::parse(ONE).expect("parse");
        let grant = policy.decide("r1", "api.github.com").expect("allow");
        assert_eq!(grant, &Grant::Github { repos: vec!["sandy".to_owned()], write: true });
    }

    #[test]
    fn policy_should_deny_when_the_host_differs() {
        let policy = Policy::parse(ONE).expect("parse");
        assert_eq!(policy.decide("r1", "github.com"), Err(Denied::NoRule));
    }

    #[test]
    fn policy_should_deny_when_the_subject_differs() {
        let policy = Policy::parse(ONE).expect("parse");
        assert_eq!(policy.decide("r2", "api.github.com"), Err(Denied::NoRule));
    }

    #[test]
    fn policy_should_not_match_a_subject_by_prefix() {
        let policy = Policy::parse(ONE).expect("parse");
        assert_eq!(policy.decide("r1-evil", "api.github.com"), Err(Denied::NoRule));
    }

    #[test]
    fn policy_should_refuse_to_load_when_there_are_no_rules() {
        assert!(matches!(Policy::parse(r#"{"rules":[]}"#), Err(PolicyError::Empty)));
    }

    #[test]
    fn policy_should_refuse_to_load_when_the_json_is_malformed() {
        assert!(matches!(Policy::parse("not json"), Err(PolicyError::Invalid(_))));
    }
}
