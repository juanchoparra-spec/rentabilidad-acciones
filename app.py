"""
Rentabilidad de acciones y ETFs desde Yahoo Finance.
Ejecutar:  pip install -r requirements.txt   y luego   streamlit run app.py
"""
import datetime as dt
from urllib.parse import urlparse

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf
from streamlit_searchbox import st_searchbox

st.set_page_config(page_title="Rentabilidad", page_icon="📈", layout="centered")
QS_URL = "https://query2.finance.yahoo.com/v10/finance/quoteSummary/"
PERIODOS = [("ytd", "YTD"), ("oneMonth", "1 mes"), ("threeMonth", "3 meses"), ("oneYear", "1 año"),
            ("threeYear", "3 años"), ("fiveYear", "5 años"), ("tenYear", "10 años"),
            ("lastBullMkt", "Último mercado alcista"), ("lastBearMkt", "Último mercado bajista")]


# ---------------------------------------------------------------- utilidades
def num(x):
    """Yahoo a veces entrega {'raw': 0.12} y a veces 0.12."""
    if isinstance(x, dict):
        x = x.get("raw")
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def pct(x):
    return None if x is None else x * 100


def fmt_pct(v):
    return "—" if v is None or pd.isna(v) else f"{v:+.2f}%"


# ---------------------------------------------------------------- datos Yahoo
@st.cache_data(ttl=3600, show_spinner=False)
def buscar(texto):
    try:
        res = yf.Search(texto, max_results=8, news_count=0).quotes
    except Exception:
        return []
    return [(r["symbol"], r.get("shortname") or r.get("longname") or r["symbol"], r.get("quoteTypeDisp") or r.get("quoteType", ""))
            for r in res if r.get("symbol")]


@st.cache_data(ttl=900, show_spinner=False)
def resumen(sym):
    """Módulos de quoteSummary: precio, perfil y 'Performance' (igual a la pestaña de Yahoo)."""
    mods = "price,quoteType,fundPerformance,fundProfile,assetProfile"
    try:
        data = yf.Ticker(sym)._data.get_raw_json(
            QS_URL + sym, params={"modules": mods, "formatted": "false", "corsDomain": "finance.yahoo.com"})
        return data["quoteSummary"]["result"][0]
    except Exception:
        return {}


@st.cache_data(ttl=900, show_spinner=False)
def historia(sym):
    h = yf.Ticker(sym).history(period="max", auto_adjust=True)
    if h.empty:
        return pd.Series(dtype=float)
    s = h["Close"]
    s.index = s.index.tz_localize(None)
    return s


def retornos_desde_historia(s):
    """Plan B para acciones: calcula retornos con precios ajustados (incluye dividendos)."""
    if s.empty:
        return {}, {}
    fin, ultimo = s.index[-1], s.iloc[-1]

    def antes(fecha):
        sub = s[s.index <= fecha]
        return sub.iloc[-1] if len(sub) and fecha >= s.index[0] else None

    def acum(fecha):
        p = antes(fecha)
        return None if p is None else ultimo / p - 1

    def anual(anios):
        r = acum(fin - pd.DateOffset(years=anios))
        return None if r is None else (1 + r) ** (1 / anios) - 1

    trailing = {
        "ytd": acum(pd.Timestamp(fin.year - 1, 12, 31)),
        "oneMonth": acum(fin - pd.DateOffset(months=1)),
        "threeMonth": acum(fin - pd.DateOffset(months=3)),
        "oneYear": acum(fin - pd.DateOffset(years=1)),
        "threeYear": anual(3), "fiveYear": anual(5), "tenYear": anual(10),
    }
    cierres = s.groupby(s.index.year).last()
    anuales = {int(y): cierres[y] / cierres[y - 1] - 1 for y in cierres.index if y - 1 in cierres.index}
    return trailing, anuales


def cargar(sym):
    r = resumen(sym)
    price, fp = r.get("price", {}), r.get("fundPerformance", {})
    info = {
        "nombre": price.get("longName") or price.get("shortName") or sym,
        "tipo": (r.get("quoteType", {}) or {}).get("quoteType") or price.get("quoteType", ""),
        "categoria": (r.get("fundProfile", {}) or {}).get("categoryName"),
        "web": (r.get("assetProfile", {}) or {}).get("website"),
        "precio": num(price.get("regularMarketPrice")),
        "cambio": num(price.get("regularMarketChange")),
        "cambio_pct": pct(num(price.get("regularMarketChangePercent"))),
        "hora": num(price.get("regularMarketTime")),
        "post": num(price.get("postMarketPrice")),
        "post_cambio": num(price.get("postMarketChange")),
        "post_pct": pct(num(price.get("postMarketChangePercent"))),
        "post_hora": num(price.get("postMarketTime")),
        "moneda": price.get("currency", "USD"),
    }

    tr, trc = fp.get("trailingReturns") or {}, fp.get("trailingReturnsCat") or {}
    if any(num(tr.get(k)) is not None for k, _ in PERIODOS):
        # ETF / fondo: datos oficiales de la pestaña Performance de Yahoo
        info["fuente"] = "Yahoo Finance (Performance)"
        info["ref"] = f"Categoría ({info['categoria']})" if info["categoria"] else "Categoría"
        info["fecha"] = num(tr.get("asOfDate"))
        info["trailing"] = [(lbl, pct(num(tr.get(k))), pct(num(trc.get(k)))) for k, lbl in PERIODOS]
        po, poc = fp.get("performanceOverview") or {}, fp.get("performanceOverviewCat") or {}
        info["overview"] = [
            ("YTD", pct(num(po.get("ytdReturnPct"))), pct(num(poc.get("ytdReturnPct")))),
            ("1 año", pct(num(po.get("oneYearTotalReturn"))), pct(num(poc.get("oneYearTotalReturn")))),
            ("3 años", pct(num(po.get("threeYearTotalReturn"))), pct(num(poc.get("threeYearTotalReturn")))),
        ]
        atr = fp.get("annualTotalReturns") or {}
        cat = {int(x["year"]): pct(num(x.get("annualValue"))) for x in atr.get("returnsCat", []) if x.get("year")}
        info["anual"] = [(int(x["year"]), pct(num(x.get("annualValue"))), cat.get(int(x["year"])))
                         for x in atr.get("returns", []) if x.get("year")][:8]
    else:
        # Acción: se calcula desde el historial y se compara contra el S&P 500
        info["fuente"] = "Calculado con precios históricos de Yahoo Finance"
        info["ref"] = "S&P 500 (SPY)"
        s, b = historia(sym), historia("SPY")
        t1, a1 = retornos_desde_historia(s)
        t2, a2 = retornos_desde_historia(b)
        info["fecha"] = s.index[-1].timestamp() if not s.empty else None
        info["trailing"] = [(lbl, pct(t1.get(k)), pct(t2.get(k))) for k, lbl in PERIODOS[:7]]
        info["overview"] = [x for x in info["trailing"] if x[0] in ("YTD", "1 año", "3 años")]
        info["anual"] = [(y, pct(a1[y]), pct(a2.get(y))) for y in sorted(a1, reverse=True)][:8]
        if info["precio"] is None and not s.empty:
            info["precio"] = s.iloc[-1]
    return info


# ---------------------------------------------------------------- interfaz
def logo(info, sym):
    if info.get("web"):
        dominio = urlparse(info["web"]).netloc.replace("www.", "")
        st.image(f"https://www.google.com/s2/favicons?domain={dominio}&sz=128", width=56)
    else:
        st.markdown(
            f"<div style='width:56px;height:56px;border-radius:50%;background:#1C3F94;color:#fff;"
            f"display:grid;place-items:center;font-weight:800'>{sym[:4]}</div>", unsafe_allow_html=True)


def tabla(filas, sym, ref):
    df = pd.DataFrame(filas, columns=["Período", sym, ref])
    df["Diferencia"] = df[sym] - df[ref]
    color = lambda v: "" if pd.isna(v) else ("color:#1D7552" if v >= 0 else "color:#B23E33")
    sty = (df.style.format({c: fmt_pct for c in (sym, ref, "Diferencia")})
           .map(color, subset=[sym, "Diferencia"]))
    st.dataframe(sty, hide_index=True, use_container_width=True)


def senal(info):
    """Semáforo de desempeño histórico frente a su referencia (no es una recomendación)."""
    tr = {p: (v, c) for p, v, c in info["trailing"]}
    criterios = []

    def comparar(periodo, texto):
        v, c = tr.get(periodo, (None, None))
        if v is not None and c is not None:
            criterios.append((v >= c, f"{texto}: {fmt_pct(v)} vs {fmt_pct(c)}"))

    comparar("1 año", "Supera a su referencia en 1 año")
    comparar("3 años", "Supera a su referencia en 3 años (anualizado)")
    comparar("5 años", "Supera a su referencia en 5 años (anualizado)")
    comparar("10 años", "Supera a su referencia en 10 años (anualizado)")

    pares = [(v, c) for _, v, c in info["anual"] if v is not None and c is not None]
    if len(pares) >= 4:
        ganados = sum(v >= c for v, c in pares)
        criterios.append((ganados / len(pares) >= 0.6,
                          f"Constancia: le ganó a su referencia {ganados} de {len(pares)} años"))
        peor_v, peor_c = min(v for v, _ in pares), min(c for _, c in pares)
        criterios.append((peor_v >= peor_c - 5,
                          f"Riesgo: su peor año ({fmt_pct(peor_v)}) no fue mucho peor que el de su referencia ({fmt_pct(peor_c)})"))

    if len(criterios) < 3:
        return None
    puntos = sum(ok for ok, _ in criterios)
    ratio = puntos / len(criterios)
    if ratio >= 0.8:
        nivel = ("🟢 Desempeño histórico fuerte", st.success)
    elif ratio >= 0.5:
        nivel = ("🟡 Desempeño histórico mixto", st.warning)
    else:
        nivel = ("🔴 Desempeño histórico débil", st.error)
    return nivel, puntos, criterios


def fecha(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%d/%m/%Y") if ts else "—"


st.title("📈 Rentabilidad de acciones y ETFs")
def sugerencias(texto):
    """Sugerencias en vivo desde Yahoo Finance mientras escribes (nombre o ticker)."""
    texto = (texto or "").strip()
    if not texto:
        return []
    return [(f"{s}  ·  {n}  ({t})", s) for s, n, t in buscar(texto)]


elegido = st_searchbox(
    sugerencias,
    placeholder="Escribe un nombre o ticker: Tesla, Apple, QQQ…",
    key="buscador",
)
if elegido:
    st.session_state["sym"] = elegido
if "sym" not in st.session_state:
    st.info("Escribe el nombre de la empresa o su ticker y elige una opción de la lista.")
    st.stop()

sym = st.session_state["sym"]

with st.spinner(f"Consultando {sym} en Yahoo Finance…"):
    info = cargar(sym)

if info["precio"] is None:
    st.error(f"Yahoo Finance no devolvió datos para {sym}. Intenta de nuevo en unos minutos.")
    st.stop()

# 1. Cotización
c1, c2 = st.columns([1, 6])
with c1:
    logo(info, sym)
with c2:
    st.subheader(f"{info['nombre']} ({sym})")
    st.caption(f"{info['tipo']}{' · ' + info['categoria'] if info['categoria'] else ''} · Retornos al {fecha(info['fecha'])}")

m1, m2 = st.columns(2)
delta = None if info["cambio"] is None else f"{info['cambio']:+.2f} ({info['cambio_pct']:+.2f}%)"
m1.metric("Precio de cierre", f"${info['precio']:,.2f}", delta)
if info["post"]:
    m2.metric("Post-mercado", f"${info['post']:,.2f}",
              f"{info['post_cambio']:+.2f} ({info['post_pct']:+.2f}%)" if info["post_cambio"] is not None else None)

# Señal de desempeño histórico
res = senal(info)
if res:
    (titulo, caja), puntos, criterios = res
    caja(f"**{titulo}** · cumple {puntos} de {len(criterios)} criterios frente a {info['ref']}")
    with st.expander("Ver por qué"):
        for ok, texto in criterios:
            st.markdown(f"{'✅' if ok else '❌'} {texto}")
        st.caption("Evalúa solo el rendimiento pasado frente a su referencia. No predice el futuro "
                   "ni considera tu perfil, plazo o valorización actual; no es una recomendación de compra o venta.")

# 2. Performance Overview
st.markdown("### Resumen de rendimiento")
tabla(info["overview"], sym, info["ref"])

# 3. Trailing returns
st.markdown("### Retornos vs referencia")
st.caption("3, 5 y 10 años anualizados.")
tabla(info["trailing"], sym, info["ref"])

# 4. Retornos anuales
if info["anual"]:
    st.markdown("### Retorno total anual")
    anos = [str(a) for a, _, _ in reversed(info["anual"])]
    v1 = [v for _, v, _ in reversed(info["anual"])]
    v2 = [c for _, _, c in reversed(info["anual"])]
    fig = go.Figure([
        go.Bar(name=sym, x=anos, y=v1, marker_color=["#2B47C9" if (v or 0) >= 0 else "#B23E33" for v in v1],
               text=[fmt_pct(v) for v in v1], textposition="outside"),
        go.Bar(name=info["ref"], x=anos, y=v2, marker_color="#9AA5B5"),
    ])
    fig.update_layout(barmode="group", height=360, margin=dict(l=0, r=0, t=10, b=0),
                      yaxis_ticksuffix="%", legend=dict(orientation="h", y=-0.15))
    st.plotly_chart(fig, use_container_width=True)
    tabla([(str(a), v, c) for a, v, c in info["anual"]], sym, info["ref"])

st.caption(f"Fuente: {info['fuente']}. Datos con fines informativos, no constituyen asesoría de inversión.")
