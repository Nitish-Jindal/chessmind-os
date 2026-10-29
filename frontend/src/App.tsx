import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import { Home } from "./pages/Home";
import { ImportPage } from "./pages/Import";
import { Mastery } from "./pages/Mastery";
import { Openings } from "./pages/Openings";
import { Opponents } from "./pages/Opponents";
import { Session } from "./pages/Session";

export function App() {
  const { player, loading, logout } = useAuth();
  if (loading) return <p className="page">Loading…</p>;
  return (
    <>
      <header className="nav">
        <strong>ChessMind OS</strong>
        <nav aria-label="Main">
          <NavLink to="/" end>Home</NavLink>
          {player && (
            <>
              <NavLink to="/import">Import</NavLink>
              <NavLink to="/openings">Openings</NavLink>
              <NavLink to="/mastery">Mastery</NavLink>
              <NavLink to="/opponents">Opponents</NavLink>
            </>
          )}
        </nav>
        {player && (
          <span className="who">
            {player.display_name} <button onClick={logout}>Log out</button>
          </span>
        )}
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/import" element={player ? <ImportPage /> : <Navigate to="/" />} />
          <Route path="/openings" element={player ? <Openings /> : <Navigate to="/" />} />
          <Route path="/session/:id" element={player ? <Session /> : <Navigate to="/" />} />
          <Route path="/mastery" element={player ? <Mastery /> : <Navigate to="/" />} />
          <Route path="/opponents" element={player ? <Opponents /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </>
  );
}
