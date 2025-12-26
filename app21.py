import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import math
from datetime import datetime
from fpdf import FPDF
import re

# -----------------------------------------------------------------------------
# 1. CONFIGURATION & STYLING
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="FinSight AI | Forensic Intelligence",
    page_icon="🦅",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize Session State
state_keys = ['d_main', 'm_main', 'd_peer', 'm_peer', 'chat_history', 'ai_verdict']
for key in state_keys:
    if key not in st.session_state:
        st.session_state[key] = None if key != 'chat_history' else []

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
    .stApp { background: #f4f6f8; font-family: 'Inter', sans-serif; }
    
    /* Card Styles */
    .metric-card {
        background: white; padding: 20px; border-radius: 10px;
        box-shadow: 0 2px 5px rgba(0,0,0,0.05); border-left: 4px solid #1e3c72;
        margin-bottom: 10px; transition: all 0.3s ease;
    }
    .metric-card:hover { transform: translateY(-2px); box-shadow: 0 5px 15px rgba(0,0,0,0.1); }
    .label { font-size: 12px; color: #666; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
    .value { font-size: 24px; font-weight: 700; color: #222; margin-top: 5px; }
    
    /* Verdict Banner */
    .verdict-banner {
        padding: 20px; border-radius: 12px; color: white; text-align: center;
        margin-bottom: 25px; box-shadow: 0 4px 15px rgba(0,0,0,0.1);
    }
    
    /* Chatbot UI */
    .chat-container {
        background: white; border: 1px solid #eee; border-radius: 10px;
        padding: 15px; height: 350px; overflow-y: auto;
        display: flex; flex-direction: column-reverse;
    }
    .user-msg { 
        align-self: flex-end; background: #e3f2fd; color: #1565c0; 
        padding: 8px 12px; border-radius: 12px 12px 0 12px; margin: 5px; font-size: 14px;
        max-width: 80%;
    }
    .bot-msg { 
        align-self: flex-start; background: #f5f5f5; color: #333; 
        padding: 8px 12px; border-radius: 12px 12px 12px 0; margin: 5px; font-size: 14px;
        max-width: 80%;
    }
    
    /* Footer */
    .footer {
        position: fixed; bottom: 0; left: 0; width: 100%;
        background: #fff; color: #555; text-align: center;
        padding: 8px; font-size: 12px; border-top: 1px solid #eee; z-index: 999;
    }
</style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 2. ROBUST DATA ENGINE (DEEP FETCH)
# -----------------------------------------------------------------------------

def safe_float(val):
    try:
        if val is None or pd.isna(val): return None
        return float(val)
    except: return None

def normalize_index(df):
    if df is None or df.empty: return df
    df.index = df.index.astype(str).str.replace(r'[^a-zA-Z0-9]', '', regex=True).str.lower()
    return df

def get_value(df, possible_names, col_index=0):
    """Deep search for values in dataframe using fuzzy matching."""
    if df is None or df.empty: return None
    
    # Map cleaned keys to actual index
    index_map = {str(idx).replace(r'[^a-zA-Z0-9]', '').lower().strip(): idx for idx in df.index}
    
    found_key = None
    for name in possible_names:
        clean = name.replace(" ", "").lower()
        if clean in index_map:
            found_key = index_map[clean]
            break
        # Partial match
        for k, v in index_map.items():
            if clean in k:
                found_key = v
                break
        if found_key: break
    
    if found_key:
        try:
            if col_index < len(df.columns):
                return safe_float(df.loc[found_key].iloc[col_index])
        except: return None
    return None

def safe_div(n, d):
    try:
        if d is None or d == 0 or n is None: return None
        return n / d
    except: return None

@st.cache_data(ttl=3600)
def fetch_data(ticker):
    """Fetches data with fallback from Annual to Quarterly to ensure visibility."""
    try:
        tk = yf.Ticker(ticker)
        
        # 1. Fetch Standard Data
        inc = normalize_index(tk.financials)
        bal = normalize_index(tk.balance_sheet)
        cf = normalize_index(tk.cashflow)
        
        # 2. Fallback: If Annual Balance Sheet is empty/zero, get Quarterly
        # This fixes the "TATASTEEL" issue where annual data might be missing in YFinance feed
        ta = get_value(bal, ["TotalAssets", "Assets"])
        if bal.empty or ta is None or ta == 0:
            bal = normalize_index(tk.quarterly_balance_sheet)
            inc = normalize_index(tk.quarterly_financials)
            cf = normalize_index(tk.quarterly_cashflow)
            
        # 3. Last Resort: Income Statement Fallback
        if inc.empty: inc = normalize_index(tk.income_stmt)
        
        # 4. Final Data Integrity Check
        ta_final = get_value(bal, ["TotalAssets", "Assets"])
        if ta_final is None or ta_final == 0:
            return None # Graceful failure if no data exists at all
            
        info = tk.info
        return {
            "inc": inc, "bal": bal, "cf": cf, "info": info,
            "ticker": ticker,
            "industry": info.get('industry', 'Unknown'),
            "sector": info.get('sector', 'Unknown'),
            "cap": info.get('marketCap', tk.fast_info.get('market_cap', None)),
            "source": "Live"
        }
    except: return None

def process_upload(file):
    try:
        xls = pd.ExcelFile(file)
        inc = normalize_index(pd.read_excel(xls, 'Income', index_col=0))
        bal = normalize_index(pd.read_excel(xls, 'Balance', index_col=0))
        cf = normalize_index(pd.read_excel(xls, 'Cashflow', index_col=0))
        return {
            "inc": inc, "bal": bal, "cf": cf,
            "info": {"longName": "Uploaded Company"},
            "ticker": "CUSTOM", "industry": "Custom", "sector": "Custom",
            "cap": 0, "source": "Upload"
        }
    except Exception as e:
        st.error(f"Excel Error: {e}")
        return None

# -----------------------------------------------------------------------------
# 3. METRIC CALCULATOR (SAFE & COMPLETE)
# -----------------------------------------------------------------------------

def calculate_metrics(d):
    r = {}
    
    # --- RAW DATA ---
    ta = get_value(d['bal'], ["TotalAssets", "Assets"])
    ca = get_value(d['bal'], ["CurrentAssets"])
    cl = get_value(d['bal'], ["CurrentLiabilities"])
    tl = get_value(d['bal'], ["TotalLiabilities", "TotalLiab"])
    eq = get_value(d['bal'], ["StockholdersEquity", "TotalEquity"])
    inv = get_value(d['bal'], ["Inventory"])
    rec = get_value(d['bal'], ["Receivables", "NetReceivables"])
    pay = get_value(d['bal'], ["Payables", "AccountsPayable"])
    debt = get_value(d['bal'], ["TotalDebt"])
    cash = get_value(d['bal'], ["Cash", "CashAndEquivalents"])
    fa = get_value(d['bal'], ["NetPPE", "FixedAssets", "PlantProperty"])
    re = get_value(d['bal'], ["RetainedEarnings"])
    
    rev = get_value(d['inc'], ["TotalRevenue", "Revenue"])
    rev_prev = get_value(d['inc'], ["TotalRevenue", "Revenue"], 1)
    cogs = get_value(d['inc'], ["CostOfRevenue", "COGS"])
    gp = get_value(d['inc'], ["GrossProfit"])
    op_inc = get_value(d['inc'], ["OperatingIncome", "EBIT"])
    ni = get_value(d['inc'], ["NetIncome"])
    int_exp = get_value(d['inc'], ["InterestExpense"])
    
    cfo = get_value(d['cf'], ["OperatingCashFlow", "TotalCashFromOperatingActivities"])
    
    # --- 1. RATIOS ---
    r['current_ratio'] = safe_div(ca, cl)
    r['quick_ratio'] = safe_div((ca - (inv if inv else 0)), cl) if (ca is not None and inv is not None) else safe_div(ca, cl)
    r['cash_ratio'] = safe_div(cash, cl)
    r['ocf_ratio'] = safe_div(cfo, cl)
    r['nwc'] = (ca - cl) if (ca is not None and cl is not None) else None
    
    r['debt_to_equity'] = safe_div(debt, eq)
    r['debt_to_assets'] = safe_div(debt, ta)
    r['equity_multiplier'] = safe_div(ta, eq)
    r['interest_coverage'] = safe_div(op_inc, abs(int_exp)) if int_exp else None
    
    r['gross_margin'] = safe_div(gp, rev)
    r['operating_margin'] = safe_div(op_inc, rev)
    r['net_margin'] = safe_div(ni, rev)
    r['roe'] = safe_div(ni, eq)
    r['roa'] = safe_div(ni, ta)
    
    # --- 2. EFFICIENCY RATIOS (ADDED) ---
    r['asset_turnover'] = safe_div(rev, ta)
    r['inv_turnover'] = safe_div(cogs, inv)
    r['rec_turnover'] = safe_div(rev, rec)
    r['pay_turnover'] = safe_div(cogs, pay)
    
    # Cycle Days (Safe Math)
    dso = safe_div(rec, rev)
    r['dso'] = dso * 365 if dso is not None else None
    
    dio = safe_div(inv, cogs)
    r['dio'] = dio * 365 if dio is not None else None
    
    dpo = safe_div(pay, cogs)
    r['dpo'] = dpo * 365 if dpo is not None else None
    
    if r['dso'] is not None and r['dio'] is not None and r['dpo'] is not None:
        r['ccc'] = r['dso'] + r['dio'] - r['dpo']
    else: r['ccc'] = None
    
    # --- 3. FORENSICS ---
    # Altman Z
    if ta:
        x1 = safe_div(r['nwc'], ta)
        x2 = safe_div(re, ta)
        x3 = safe_div(op_inc, ta)
        x4 = safe_div(d['cap'], tl) if d['cap'] else 0.0
        x5 = safe_div(rev, ta)
        if None not in [x1, x2, x3, x5]:
            r['z_score'] = 1.2*x1 + 1.4*x2 + 3.3*x3 + 0.6*x4 + 1.0*x5
        else: r['z_score'] = None
    else: r['z_score'] = None
    
    # Beneish M
    try:
        rec_prev = get_value(d['bal'], ["Receivables"], 1)
        if rev and rev_prev and rec and rec_prev:
            dsri = (rec/rev) / (rec_prev/rev_prev)
            sgi = rev / rev_prev
            r['m_score'] = -4.84 + 0.92*dsri + 0.892*sgi
        else: r['m_score'] = None
    except: r['m_score'] = None
    
    # Sloan
    accruals = (ni - cfo) if (ni is not None and cfo is not None) else None
    r['sloan_ratio'] = safe_div(accruals, ta)
    
    # Piotroski F
    f = 0
    if ni and ni > 0: f += 1
    if cfo and cfo > 0: f += 1
    if r['roa'] and r['roa'] > 0: f += 1
    if cfo and ni and cfo > ni: f += 1
    if r['current_ratio'] and r['current_ratio'] > safe_div(get_value(d['bal'],["CurrentAssets"],1), get_value(d['bal'],["CurrentLiabilities"],1)): f += 1
    r['f_score'] = f

    # Matrix Data
    rev_g = safe_div((rev - rev_prev), rev_prev) if (rev is not None and rev_prev is not None) else None
    r['rev_growth'] = rev_g * 100 if rev_g is not None else None
    r['log_assets'] = math.log(ta) if (ta and ta > 0) else None
    
    ar = safe_div(fa, ta)
    r['asset_richness'] = ar * 100 if ar is not None else None
    
    cr = safe_div(cfo, debt)
    r['cash_richness'] = cr * 100 if cr is not None else None
    
    return r

# -----------------------------------------------------------------------------
# 4. CHATBOT KNOWLEDGE BASE
# -----------------------------------------------------------------------------
def smart_chatbot(query, metrics):
    q = query.lower()
    m = metrics
    
    # Knowledge Base
    kb = {
        "z-score": "The Altman Z-Score predicts bankruptcy risk. < 1.8 is Distress, > 3.0 is Safe.",
        "m-score": "The Beneish M-Score detects earnings manipulation. > -1.78 suggests fraud.",
        "roe": "Return on Equity (ROE) measures profitability relative to shareholder equity.",
        "ccc": "Cash Conversion Cycle (CCC) measures how fast a company converts inventory to cash.",
        "current ratio": "Measures ability to pay short-term obligations.",
        "dso": "Days Sales Outstanding (DSO) is the average number of days to collect payment."
    }
    
    # Check Definitions
    for k, v in kb.items():
        if k in q and ("what" in q or "explain" in q or "define" in q): return v
        
    # Check Data
    if "z-score" in q: return f"The Z-Score is {m['z_score']:.2f}" if m['z_score'] else "N/A"
    if "m-score" in q: return f"The M-Score is {m['m_score']:.2f}" if m['m_score'] else "N/A"
    if "roe" in q: return f"ROE is {m['roe']:.2%}" if m['roe'] else "N/A"
    if "current ratio" in q: return f"Current Ratio is {m['current_ratio']:.2f}" if m['current_ratio'] else "N/A"
    if "margin" in q: return f"Net Margin is {m['net_margin']:.2%}" if m['net_margin'] else "N/A"
    
    return "I can define financial terms (e.g., 'What is ROE?') or show data (e.g., 'Show Z-Score')."

# -----------------------------------------------------------------------------
# 5. SYNTHETIC ANALYST (VERDICT ENGINE)
# -----------------------------------------------------------------------------
def generate_verdict(m):
    flags = 0
    text = []
    
    if m['z_score'] and m['z_score'] < 1.8:
        flags += 1; text.append("Financial distress risk detected (Z-Score < 1.8).")
    if m['m_score'] and m['m_score'] > -1.78:
        flags += 1; text.append("Earnings manipulation risk detected (M-Score > -1.78).")
    if m['f_score'] and m['f_score'] < 4:
        flags += 1; text.append("Weak fundamental health (Piotroski F-Score < 4).")
    if m['sloan_ratio'] and abs(m['sloan_ratio']) > 0.1:
        flags += 1; text.append("High accruals detected (Sloan Ratio > 10%).")
        
    if flags >= 2: return "HIGH RISK / DISTRESS", " ".join(text)
    elif flags == 1: return "MODERATE RISK", " ".join(text)
    else: return "LOW RISK / HEALTHY", "Company shows strong fundamentals with no major forensic flags."

# -----------------------------------------------------------------------------
# 6. PLOTTING & PDF
# -----------------------------------------------------------------------------
def plot_matrix(x, y, xtitle, ytitle, title, ticker_name, labels, px=None, py=None, pname=None):
    if x is None or y is None:
        fig = go.Figure()
        fig.update_layout(title=f"{title} (Data Unavailable)", xaxis={'visible':False}, yaxis={'visible':False}, height=300)
        return fig
        
    fig = go.Figure()
    # Quadrants
    fig.add_shape(type="rect", x0=0, y0=50, x1=50, y1=100, fillcolor="#f1c40f", opacity=0.1, layer="below", line_width=0)
    fig.add_shape(type="rect", x0=50, y0=50, x1=100, y1=100, fillcolor="#2ecc71", opacity=0.1, layer="below", line_width=0)
    fig.add_shape(type="rect", x0=0, y0=0, x1=50, y1=50, fillcolor="#e74c3c", opacity=0.1, layer="below", line_width=0)
    fig.add_shape(type="rect", x0=50, y0=0, x1=100, y1=50, fillcolor="#3498db", opacity=0.1, layer="below", line_width=0)
    
    fig.add_trace(go.Scatter(x=[x], y=[y], mode='markers+text', marker=dict(size=25, color='#2c3e50'), text=[ticker_name], textposition="top center", name=ticker_name))
    
    if px is not None and py is not None:
        fig.add_trace(go.Scatter(x=[px], y=[py], mode='markers+text', marker=dict(size=20, color='#e74c3c'), text=[pname], textposition="bottom center", name=pname))
    
    # Corner Annotations (No overlap)
    fig.add_annotation(x=0.02, y=0.98, xref="paper", yref="paper", text=labels[0], showarrow=False, font=dict(size=10, color="#d35400"), align="left")
    fig.add_annotation(x=0.98, y=0.98, xref="paper", yref="paper", text=labels[1], showarrow=False, font=dict(size=10, color="green"), align="right")
    fig.add_annotation(x=0.02, y=0.02, xref="paper", yref="paper", text=labels[2], showarrow=False, font=dict(size=10, color="red"), align="left")
    fig.add_annotation(x=0.98, y=0.02, xref="paper", yref="paper", text=labels[3], showarrow=False, font=dict(size=10, color="blue"), align="right")
    
    fig.update_layout(title=title, xaxis=dict(title=xtitle, range=[0, 100]), yaxis=dict(title=ytitle, range=[0, 100]), height=400, margin=dict(l=20,r=20,t=40,b=20), plot_bgcolor='white')
    return fig

class PDFReport(FPDF):
    def header(self):
        self.set_font('Arial', 'B', 16); self.cell(0, 10, 'FinSight AI Analysis', 0, 1, 'C'); self.ln(5)
    def footer(self):
        self.set_y(-15); self.set_font('Arial', 'I', 8); self.cell(0, 10, 'RONAK RATHI | Academic Project', 0, 0, 'C')

def generate_pdf(d, m, verdict_title, verdict_summary, d_peer=None, m_peer=None):
    pdf = PDFReport(); pdf.add_page(); pdf.set_font("Arial", size=11)
    def safe(s): return str(s).encode('latin-1', 'replace').decode('latin-1')
    def fmt(v, p=False): return "N/A" if v is None else (f"{v:.2f}%" if p else f"{v:.2f}")
    
    pdf.set_font("Arial", 'B', 14); pdf.cell(0, 10, f"Target: {safe(d['ticker'])}", 0, 1)
    pdf.set_font("Arial", size=10); pdf.cell(0, 6, f"Industry: {safe(d['industry'])}", 0, 1); pdf.ln(5)
    
    pdf.set_fill_color(220, 220, 220); pdf.set_font("Arial", 'B', 12)
    pdf.cell(0, 10, f"VERDICT: {verdict_title}", 1, 1, 'C', 1)
    pdf.set_font("Arial", 'I', 10); pdf.multi_cell(0, 6, safe(verdict_summary), 0, 'C'); pdf.ln(5)
    
    pdf.set_font("Arial", 'B', 12); pdf.cell(0, 8, "Forensic Scores", 0, 1)
    pdf.set_font("Arial", size=10)
    data = [["Z-Score", fmt(m['z_score'])], ["M-Score", fmt(m['m_score'])], ["F-Score", f"{m['f_score']}/9"], ["Sloan", fmt(m['sloan_ratio'], True)]]
    for r in data: pdf.cell(60, 7, r[0], 1); pdf.cell(60, 7, r[1], 1, 1)
    pdf.ln(5)
    
    if d_peer:
        pdf.set_font("Arial", 'B', 12); pdf.cell(0, 8, f"Peer: {safe(d_peer['ticker'])}", 0, 1)
        pdf.set_font("Arial", size=10)
        pdf.cell(60, 7, "Metric", 1); pdf.cell(60, 7, d['ticker'], 1); pdf.cell(60, 7, d_peer['ticker'], 1, 1)
        comp = [["ROE", fmt(m['roe'],1), fmt(m_peer['roe'],1)], ["Debt/Eq", fmt(m['debt_to_equity']), fmt(m_peer['debt_to_equity'])]]
        for r in comp: pdf.cell(60, 7, r[0], 1); pdf.cell(60, 7, r[1], 1); pdf.cell(60, 7, r[2], 1, 1)
        
    return pdf.output(dest='S').encode('latin-1')

def display_card(label, value, suffix="", risk_check=None):
    color = "#2c3e50"
    if risk_check == 'high_bad' and value and value > 0.1: color = "#e74c3c"
    elif risk_check == 'low_bad' and value and value < 1.8: color = "#e74c3c"
    val_str = "N/A" if value is None else f"{value:.2f}{suffix}"
    st.markdown(f"""<div class="metric-card"><div class="label">{label}</div><div class="value" style="color:{color}">{val_str}</div></div>""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# 7. MAIN APP LAYOUT
# -----------------------------------------------------------------------------

with st.sidebar:
    st.title("🦅 FinSight AI")
    mode = st.radio("Mode", ["Live Data", "Excel Upload"])
    if mode == "Live Data":
        t = st.text_input("Target Ticker", "TATASTEEL.NS").upper()
        if st.button("Load Target"):
            with st.spinner("Fetching..."):
                st.session_state.d_main = fetch_data(t)
                st.session_state.m_main = calculate_metrics(st.session_state.d_main) if st.session_state.d_main else None
                st.session_state.ai_verdict = None
                if not st.session_state.d_main: st.error("Data Not Found")
    else:
        f = st.file_uploader("Upload Excel", type=["xlsx"])
        if f:
            st.session_state.d_main = process_upload(f)
            st.session_state.m_main = calculate_metrics(st.session_state.d_main)

    st.divider()
    
    # Smart Chatbot
    st.markdown("### 💬 Data Assistant")
    user_q = st.text_input("Ask a question:")
    if st.button("Ask"):
        if st.session_state.m_main:
            ans = smart_chatbot(user_q, st.session_state.m_main)
            st.session_state.chat_history.insert(0, (user_q, ans))
            
    if st.session_state.chat_history:
        st.markdown('<div class="chat-container">', unsafe_allow_html=True)
        for q, a in st.session_state.chat_history:
            st.markdown(f"<div class='user-msg'>{q}</div><div class='bot-msg'>🦅 {a}</div>", unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    st.divider()
    pt = st.text_input("Peer Ticker", "JSL.NS").upper()
    if st.button("Run Comparison"):
        if st.session_state.d_main:
            with st.spinner("Comparing..."):
                d_peer = fetch_data(pt)
                if d_peer:
                    i1 = st.session_state.d_main.get('industry', 'Unknown')
                    i2 = d_peer.get('industry', 'Unknown')
                    if i1 != 'Unknown' and i2 != 'Unknown' and i1 != i2:
                        st.error(f"⛔ Mismatch: {i1} vs {i2}")
                        st.session_state.d_peer = None
                    else:
                        st.session_state.d_peer = d_peer
                        st.session_state.m_peer = calculate_metrics(d_peer)
                        st.success("Compared!")
                else: st.error("Peer Not Found")
        else: st.warning("Load Target First")

# --- DASHBOARD ---
if st.session_state.d_main and st.session_state.m_main:
    d = st.session_state.d_main
    m = st.session_state.m_main
    
    # Verdict
    if not st.session_state.ai_verdict:
        st.session_state.ai_verdict = generate_verdict(m)
    v_title, v_sum = st.session_state.ai_verdict
    color = "#27ae60" if "LOW" in v_title else "#f1c40f" if "MODERATE" in v_title else "#c0392b"
    
    st.markdown(f"## 📊 Report: {d['info'].get('longName', d['ticker'])}")
    st.markdown(f"""<div class="verdict-banner" style="background:{color}"><h2>VERDICT: {v_title}</h2><p>{v_sum}</p></div>""", unsafe_allow_html=True)
    
    t1, t2, t3, t4, t5 = st.tabs(["🔍 Forensics", "📈 Matrices", "🧮 Ratios", "⚖️ Comparison", "📥 Report"])
    
    with t1:
        c1, c2, c3, c4 = st.columns(4)
        with c1: display_card("Altman Z", m['z_score'], risk_check='low_bad')
        with c2: display_card("Beneish M", m['m_score'], risk_check='high_bad')
        with c3: display_card("Piotroski F", m['f_score'], "/9")
        with c4: display_card("Sloan Ratio", m['sloan_ratio'], "%", risk_check='high_bad')
        
    with t2:
        c1, c2 = st.columns(2)
        # Peer
        px_b, py_b, px_d, py_d, pname = None, None, None, None, None
        if st.session_state.d_peer:
            mp = st.session_state.m_peer
            pname = st.session_state.d_peer['ticker']
            if mp['log_assets']: px_b = min(100, max(0, mp['log_assets']*3))
            if mp['rev_growth']: py_b = min(100, max(0, mp['rev_growth']+50))
            if mp['asset_richness']: px_d = mp['asset_richness']
            if mp['cash_richness']: py_d = mp['cash_richness']

        with c1:
            sz = min(100, max(0, (m['log_assets'] if m['log_assets'] else 0)*3)) if m['log_assets'] else None
            gr = min(100, max(0, (m['rev_growth'] if m['rev_growth'] else 0)+50)) if m['rev_growth'] else None
            st.plotly_chart(plot_matrix(sz, gr, "Size", "Growth", "BCG Proxy", d['ticker'], ["QM", "Stars", "Dogs", "Cows"], px_b, py_b, pname), use_container_width=True)
        with c2:
            st.plotly_chart(plot_matrix(m['asset_richness'], m['cash_richness'], "Assets%", "Cash%", "Debt Matrix", d['ticker'], ["Avalanche", "Sculpting", "Snowball", "Sizing"], px_d, py_d, pname), use_container_width=True)

    with t3:
        st.subheader("1. Liquidity"); c1,c2,c3,c4 = st.columns(4)
        with c1: display_card("Current", m['current_ratio'])
        with c2: display_card("Quick", m['quick_ratio'])
        with c3: display_card("Cash", m['cash_ratio'])
        with c4: display_card("OCF Ratio", m['ocf_ratio'])
        
        st.subheader("2. Profitability"); c1,c2,c3,c4 = st.columns(4)
        with c1: display_card("Gross Margin", m['gross_margin'], "%")
        with c2: display_card("Net Margin", m['net_margin'], "%")
        with c3: display_card("ROE", m['roe'], "%")
        with c4: display_card("ROA", m['roa'], "%")
        
        st.subheader("3. Efficiency"); c1,c2,c3,c4 = st.columns(4)
        with c1: display_card("Asset Turnover", m['asset_turnover'])
        with c2: display_card("Inv. Turnover", m['inv_turnover'])
        with c3: display_card("Rec. Turnover", m['rec_turnover'])
        with c4: display_card("CCC (Days)", m['ccc'])

    with t4:
        if st.session_state.d_peer:
            mp = st.session_state.m_peer
            st.success(f"Comparing: {d['ticker']} vs {st.session_state.d_peer['ticker']}")
            def f(v, p=False): return "N/A" if v is None else (f"{v:.2f}%" if p else f"{v:.2f}")
            comp = pd.DataFrame({
                "Metric": ["Z-Score", "M-Score", "ROE", "Net Margin", "Asset Turnover"],
                d['ticker']: [f(m['z_score']), f(m['m_score']), f(m['roe'],1), f(m['net_margin'],1), f(m['asset_turnover'])],
                st.session_state.d_peer['ticker']: [f(mp['z_score']), f(mp['m_score']), f(mp['roe'],1), f(mp['net_margin'],1), f(mp['asset_turnover'])]
            }).set_index("Metric")
            st.table(comp)
        else: st.info("Run Comparison from Sidebar.")

    with t5:
        if st.button("Download PDF"):
            try:
                pdf_bytes = generate_pdf(d, m, v_title, v_sum, st.session_state.d_peer, st.session_state.m_peer)
                st.download_button("Click to Download", pdf_bytes, "Report.pdf", "application/pdf")
                st.success("Done!")
            except Exception as e: st.error(f"PDF Error: {e}")

st.markdown('<div class="footer">Developed by <b>RONAK RATHI</b> | Academic Project Purpose Only</div>', unsafe_allow_html=True)