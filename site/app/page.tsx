import Image from "next/image";

const features = [
  {
    index: "01",
    title: "跨机器记忆归档",
    copy: "Codex CLI、Desktop 与 Remote 主机通过 MCP 将完整对话、决策和上下文汇总到同一服务端。",
  },
  {
    index: "02",
    title: "混合召回",
    copy: "融合精确短语、BM25、模糊匹配、元数据和可选向量相似度，返回可直接注入 Codex 的历史上下文。",
  },
  {
    index: "03",
    title: "Session Trace",
    copy: "按用户请求还原 Codex 响应与工具调用路径，区分精确耗时和估算耗时，并追溯到完整聊天。",
  },
  {
    index: "04",
    title: "工具 FAQ",
    copy: "成功工具沉淀为可召回经验；失败工具保留证据、错误分类、处理建议和稳定失败签名。",
  },
];

const flow = [
  ["Capture", "完整聊天与 Observation 首先原样入库"],
  ["Compress", "规则或可选 LLM 提炼长期知识"],
  ["Consolidate", "同类型 Memory 归并并保留来源"],
  ["Recall", "混合检索返回可验证的历史上下文"],
];

export default function Home() {
  return (
    <main>
      <header className="site-header">
        <a className="brand" href="#top" aria-label="OPEM 首页">
          <span className="brand-mark" aria-hidden="true"><i /><i /><i /></span>
          <span>OPEM</span>
        </a>
        <nav aria-label="页面导航">
          <a href="#features">能力</a>
          <a href="#flow">架构</a>
          <a href="#screens">界面</a>
          <a href="#deploy">部署</a>
        </nav>
        <a className="header-link" href="https://github.com/dawncc/OPEM">GitHub ↗</a>
      </header>

      <section className="hero" id="top">
        <div className="hero-glow" aria-hidden="true" />
        <div className="hero-copy">
          <div className="status-pill"><span /> Python · MCP · Self-hosted</div>
          <p className="eyebrow">One Personal Evolving Memory System</p>
          <h1>让每一次 Codex 工作<br />都能被重新找到。</h1>
          <p className="hero-lead">把分散在不同服务器上的完整对话、工具结果和关键决策汇总到统一 Memory Server，并以可追溯的方式召回。</p>
          <div className="hero-actions">
            <a className="button primary" href="#screens">查看真实界面</a>
            <a className="button" href="https://github.com/dawncc/OPEM#quick-start-local-sqlite">本机开始部署</a>
          </div>
          <div className="hero-facts" aria-label="项目能力概览">
            <div><strong>5</strong><span>MCP 工具</span></div>
            <div><strong>2</strong><span>数据库模式</span></div>
            <div><strong>4+</strong><span>检索信号</span></div>
            <div><strong>100%</strong><span>来源可追溯</span></div>
          </div>
        </div>
        <div className="hero-visual">
          <div className="visual-window">
            <div className="window-bar"><span className="window-dots"><i /><i /><i /></span><span>memory://overview</span><b>LIVE</b></div>
            <Image src="/screenshots/overview.png" alt="OPEM 系统概览" width={1265} height={712} priority />
          </div>
          <div className="float-card float-recall"><span>Recall</span><strong>历史上下文已命中</strong><small>BM25 · fuzzy · metadata</small></div>
          <div className="float-card float-trace"><span>Trace</span><strong>848 ms</strong><small>4 次工具调用</small></div>
        </div>
      </section>

      <section className="signal-strip" aria-label="技术栈">
        <span>FastAPI</span><i /> <span>SQLite / PostgreSQL</span><i /> <span>pgvector</span><i /> <span>Jinja2</span><i /> <span>OpenAI-compatible</span>
      </section>

      <section className="section" id="features">
        <div className="section-heading"><div><p className="eyebrow">Built for continuity</p><h2>不是聊天备份，<br />而是可工作的长期记忆。</h2></div><p>原始对话永不被摘要覆盖；长期 Memory 与来源 Session 始终保持关联，Codex 可以召回，但不会把历史内容当成当前指令。</p></div>
        <div className="feature-grid">{features.map((feature) => <article className="feature-card" key={feature.index}><span>{feature.index}</span><h3>{feature.title}</h3><p>{feature.copy}</p></article>)}</div>
      </section>

      <section className="section flow-section" id="flow">
        <div className="flow-copy"><p className="eyebrow">Memory lifecycle</p><h2>从一次请求，<br />到可验证的经验。</h2><p>轻量 Worker 负责压缩、归并与索引。未配置 LLM 或 embedding 时，系统仍可使用规则压缩和全文检索运行。</p></div>
        <div className="flow-list">{flow.map(([title, copy], index) => <article key={title}><span>{String(index + 1).padStart(2, "0")}</span><div><h3>{title}</h3><p>{copy}</p></div></article>)}</div>
      </section>

      <section className="section screenshot-section" id="screens">
        <div className="section-heading"><div><p className="eyebrow">Real product screen</p><h2>所有记忆，<br />一个入口。</h2></div><p>主页面集中展示项目、Session、Memory 与最近活动。需要深入时，再进入搜索、Trace、工具归档和 FAQ。</p></div>
        <figure className="overview-shot"><div className="screenshot-frame"><Image src="/screenshots/overview.png" alt="OPEM 系统概览主页面" width={1265} height={712} /></div><figcaption><span>01</span><div><strong>Memory Server Overview</strong><p>从一个页面掌握跨项目记忆状态、活动趋势和最近沉淀的长期知识。</p></div></figcaption></figure>
      </section>

      <section className="section deploy-section" id="deploy">
        <div className="deploy-copy"><p className="eyebrow">Deployment boundary</p><h2>展示站上 Sites，<br />Memory 服务留在你的网络。</h2><p>当前 Sites 版本用于产品说明和界面展示。FastAPI、Worker 与数据库需要部署在局域网服务器、VPN 或其他可运行 Python 的环境中。</p><a className="button primary" href="https://github.com/dawncc/OPEM">查看部署文档 ↗</a></div>
        <div className="deploy-stack">
          <article><span className="stack-badge cloud">Sites</span><div><strong>公开展示层</strong><p>产品介绍 · 架构 · 真实界面</p></div><b>已适配</b></article>
          <div className="stack-connector"><span>HTTP / private network</span></div>
          <article><span className="stack-badge server">PY</span><div><strong>Memory Server</strong><p>FastAPI · Worker · SQLite / PostgreSQL</p></div><b>独立部署</b></article>
        </div>
      </section>

      <footer><div className="brand"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span><span>OPEM</span></div><p>One Personal Evolving Memory System</p><a href="https://github.com/dawncc/OPEM">Source on GitHub ↗</a></footer>
    </main>
  );
}
