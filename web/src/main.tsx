import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { TickerIndex } from "./TickerIndex";
import { TickerPage } from "./TickerPage";
import "./styles.css";

// Hash routes: #/ (all tickers) and #/t/MSFT (one ticker).
function useRoute(): string {
  const [hash, setHash] = useState(window.location.hash || "#/");
  useEffect(() => {
    const onChange = () => setHash(window.location.hash || "#/");
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

function App() {
  const route = useRoute();
  const ticker = route.match(/^#\/t\/([^/?#]+)/);
  return (
    <div className="shell">
      <header className="topbar">
        <a href="#/" className="brand">
          TX·DC TRACKER
        </a>
        <span className="dim">Texas data center filings → sites → companies → tickers</span>
      </header>
      {ticker ? <TickerPage symbol={decodeURIComponent(ticker[1])} /> : <TickerIndex />}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
