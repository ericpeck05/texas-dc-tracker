import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { FeedPage } from "./FeedPage";
import { ReviewPage } from "./ReviewPage";
import { SiteList } from "./SiteList";
import { SupplierPage } from "./SupplierPage";
import { SitePage } from "./SitePage";
import { TickerIndex } from "./TickerIndex";
import { TickerPage } from "./TickerPage";
import "./styles.css";

// Hash routes: #/ (feed), #/sites (map + site list), #/s/12 (one site), #/tickers, #/t/MSFT,
// #/suppliers, #/review.
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
  const onReview = route.startsWith("#/review");
  const onSites = Boolean(site) || route.startsWith("#/sites");
  const onSuppliers = route.startsWith("#/suppliers");
  const onFeed = !onTickers && !onReview && !onSites && !onSuppliers;
  let page;
  if (ticker) page = <TickerPage symbol={decodeURIComponent(ticker[1])} />;
  else if (site) page = <SitePage id={site[1]} />;
  else if (route.startsWith("#/tickers")) page = <TickerIndex />;
  else if (onReview) page = <ReviewPage />;
  else if (onSites) page = <SiteList />;
  else if (onSuppliers) page = <SupplierPage />;
  else page = <FeedPage />;
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
          <a href="#/" className={onFeed ? "here" : undefined}>
            Feed
          </a>
          <a href="#/sites" className={onSites ? "here" : undefined}>
            Map + sites
          </a>
          <a href="#/tickers" className={onTickers ? "here" : undefined}>
            Tickers
          </a>
          <a href="#/suppliers" className={onSuppliers ? "here" : undefined}>
            Suppliers
          </a>
          <a href="#/review" className={onReview ? "here" : undefined}>
            Review
          </a>
        </nav>
        <span className="dim">
          Texas data center filings → sites → companies → tickers
        </span>
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
