import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { SiteList } from "./SiteList";
import { SitePage } from "./SitePage";
import { TickerIndex } from "./TickerIndex";
import { TickerPage } from "./TickerPage";
import "./styles.css";

// Hash routes: #/ and #/sites (site list), #/s/12 (one site), #/tickers, #/t/MSFT.
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
  const site = route.match(/^#\/s\/(\d+)/);
  const onTickers = ticker || route.startsWith("#/tickers");
  let page;
  if (ticker) page = <TickerPage symbol={decodeURIComponent(ticker[1])} />;
  else if (site) page = <SitePage id={site[1]} />;
  else if (route.startsWith("#/tickers")) page = <TickerIndex />;
  else page = <SiteList />;
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [route]);
  return (
    <div className="shell">
      <header className="topbar">
        <a href="#/" className="brand">
          TX·DC TRACKER
        </a>
        <nav>
          <a href="#/sites" className={!onTickers ? "here" : undefined}>
            Sites
          </a>
          <a href="#/tickers" className={onTickers ? "here" : undefined}>
            Tickers
          </a>
        </nav>
        <span className="dim">Texas data center filings → sites → companies → tickers</span>
      </header>
      {page}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
