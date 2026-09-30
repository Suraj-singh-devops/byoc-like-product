import Link from "next/link";

export default function NotFound() {
  return (
    <div className="auth-page">
      <div className="card auth-card">
        <div className="card-body stack">
          <h1>Page not found</h1>
          <p className="muted">The page you are looking for does not exist.</p>
          <Link className="button" href="/dashboard">
            Go to the dashboard
          </Link>
        </div>
      </div>
    </div>
  );
}
