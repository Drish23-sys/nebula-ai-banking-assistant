import { useState } from "react";
import { login, signup } from "../api";

export default function LoginForm({ onAuthenticated }) {
  const [mode, setMode] = useState("login"); // "login" | "signup"
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const action = mode === "login" ? login : signup;
      const result = await action({ email, password });
      onAuthenticated(result);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <div className="auth-card__brand">
          <span className="sidebar__mark">N</span>
          <span className="sidebar__wordmark">Nebula</span>
        </div>
        <h1 className="auth-card__title">{mode === "login" ? "Welcome back" : "Create your account"}</h1>
        <p className="auth-card__subtitle">
          {mode === "login"
            ? "Log in to pick up your conversation where you left off."
            : "Sign up to start chatting with your banking assistant."}
        </p>

        <form onSubmit={submit} className="auth-form">
          <label className="auth-form__label">
            Email
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              placeholder="you@example.com"
            />
          </label>
          <label className="auth-form__label">
            Password
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              minLength={8}
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              placeholder="At least 8 characters"
            />
          </label>

          {error && <div className="auth-form__error">{error}</div>}

          <button type="submit" className="auth-form__submit" disabled={submitting}>
            {submitting ? "Please wait…" : mode === "login" ? "Log in" : "Sign up"}
          </button>
        </form>

        <button
          className="auth-card__switch"
          onClick={() => {
            setMode(mode === "login" ? "signup" : "login");
            setError(null);
          }}
        >
          {mode === "login" ? "Don't have an account? Sign up" : "Already have an account? Log in"}
        </button>
      </div>
    </div>
  );
}
