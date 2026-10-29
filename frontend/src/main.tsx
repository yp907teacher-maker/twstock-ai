import { StrictMode, useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, MemoryRouter, NavLink, Route, Routes } from "react-router-dom";
import { api, IS_STATIC, type Meta } from "./api";
import { DataDate, MetaCtx, SearchBox, ToastProvider } from "./components/ui";
import Overview from "./pages/Overview";
import Screener from "./pages/Screener";
import InstrumentPage from "./pages/Instrument";
import WatchlistPage from "./pages/Watchlist";
import Backtest from "./pages/Backtest";
import Health from "./pages/Health";
import { dt } from "./format";
import "./styles.css";

const NAV = [
  ["/", "市場總覽"],
  ["/screener", "選股工作台"],
  ["/watchlist", "自選股"],
  ["/backtest", "回測與模型"],
  ["/health", "資料健康"],
];

function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const reload = useCallback(() => {
    api.get<Meta>("/api/meta").then(setMeta).catch(() => {});
  }, []);
  useEffect(() => {
    reload();
    if (IS_STATIC) return;
    const t = window.setInterval(reload, 30000);
    return () => window.clearInterval(t);
  }, [reload]);

  return (
    <MetaCtx.Provider value={{ meta, reload }}>
      <div className="shell">
        <nav className="side" aria-label="主選單">
          <div className="brand">
            <b>台股 AI 研究台</b>
            <span>個人研究用 · 不構成投資建議</span>
          </div>
          {NAV.map(([to, label], i) => (
            <NavLink key={to} to={to} end={to === "/"}>
              <span className="idx">0{i + 1}</span>
              {label}
            </NavLink>
          ))}
          <div className="foot">
            資料：證交所／櫃買 OpenAPI、FinMind、Fugle
            <br />
            收盤後更新 {meta?.scheduler.enabled ? meta.scheduler.time : "（排程關閉）"}
          </div>
        </nav>
        <div className="main">
          {meta?.demo_mode && (
            <div className="demo-banner">
              {meta.static ? "線上展示版（唯讀）：" : "示範資料模式："}以下所有數字皆為合成資料，非真實行情，僅供檢視功能
            </div>
          )}
          {meta?.static && !meta.demo_mode && (
            <div className="live-banner">
              線上版（唯讀）· 真實行情，每個交易日收盤後自動更新 · 本次更新 {dt(meta.built_at)} · 資料：證交所／櫃買 OpenAPI、FinMind
            </div>
          )}
          <header className="topbar">
            <SearchBox />
            <div className="spacer" />
            {meta?.job.running && (
              <span className="badge info">
                {meta.job.running === "bootstrap" ? "首次建置真實資料中" : `工作執行中：${meta.job.running}`}
                {meta.progress ? `（${meta.progress.phase ?? ""} ${meta.progress.done}/${meta.progress.total} 檔）` : ""}
              </span>
            )}
            <DataDate label="行情" value={meta?.latest_price_date} />
            <DataDate label="評分" value={meta?.latest_score_date} />
          </header>
          <main className="content">
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/screener" element={<Screener />} />
              <Route path="/instrument/:id" element={<InstrumentPage />} />
              <Route path="/watchlist" element={<WatchlistPage />} />
              <Route path="/backtest" element={<Backtest />} />
              <Route path="/health" element={<Health />} />
              <Route path="*" element={<div className="empty">找不到頁面</div>} />
            </Routes>
          </main>
        </div>
      </div>
    </MetaCtx.Provider>
  );
}

// 線上展示版在沙盒框架內執行，網址路徑無法改變，因此改用記憶體路由
const Router = IS_STATIC ? MemoryRouter : BrowserRouter;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Router>
      <ToastProvider>
        <App />
      </ToastProvider>
    </Router>
  </StrictMode>,
);
