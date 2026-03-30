import requests
import smtplib
import os
import io
import time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from datetime import datetime, timezone, timedelta
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# ─── CONFIGURACIÓN ────────────────────────────────────────────────────────────
ASN = "28091"

EXPECTED_PREFIXES = [
    "190.2.88.0/21",
    "190.2.88.0/24",
    "190.2.89.0/24",
    "190.2.90.0/24",
    "190.2.91.0/24",
    "190.2.92.0/24",
    "190.2.93.0/24",
    "190.2.94.0/24",
    "190.2.95.0/24",
]

GMAIL_USER         = os.environ.get("GMAIL_USER",         "n8nnetworking@gmail.com")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "awcu xeok iuac ylai")
EMAIL_TO           = os.environ.get("EMAIL_TO",           "redes@sixmanager.com")
SEND_REPORT        = os.environ.get("SEND_REPORT", "false").lower() == "true"
SEND_STATUS        = os.environ.get("SEND_STATUS", "false").lower() == "true"
PDF_FILE           = "bgp_report.pdf"

HEADERS = {"User-Agent": "SixManager-BGP-Monitor/1.0 (redes@sixmanager.com)"}
# ──────────────────────────────────────────────────────────────────────────────

C_BLUE   = (30/255,  58/255, 138/255)
C_GREEN  = (34/255, 139/255,  34/255)
C_RED    = (200/255,  0/255,   0/255)
C_ORANGE = (255/255, 140/255,  0/255)
C_GRAY   = (150/255, 150/255, 150/255)


def now_chile():
    utc_now = datetime.now(timezone.utc)
    offset = -3 if utc_now.month in (10, 11, 12, 1, 2, 3) else -4
    return utc_now.astimezone(timezone(timedelta(hours=offset)))


# ─── FETCH DATA ───────────────────────────────────────────────────────────────

def fetch_announced_prefixes() -> set:
    """Prefijos actualmente anunciados por AS28091 via RIPE STAT."""
    url = f"https://stat.ripe.net/data/announced-prefixes/data.json?resource=AS{ASN}"
    resp = requests.get(url, timeout=20, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "ok":
        raise RuntimeError(f"RIPE STAT error: {data.get('status_code', data)}")
    return {p["prefix"] for p in data["data"].get("prefixes", [])}


def fetch_prefix_detail(prefix: str) -> dict:
    """Detalle de un prefijo via BGPView (origen ASN, RIR, etc)."""
    ip, length = prefix.split("/")
    url = f"https://api.bgpview.io/prefix/{ip}/{length}"
    try:
        resp = requests.get(url, timeout=15, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "ok":
            return data.get("data", {})
    except Exception as e:
        print(f"    BGPView {prefix}: {e}")
    return {}


def fetch_upstreams() -> list:
    """Upstreams IPv4 de AS28091 via BGPView."""
    url = f"https://api.bgpview.io/asn/{ASN}/upstreams"
    try:
        resp = requests.get(url, timeout=15, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "ok":
            return data.get("data", {}).get("ipv4_upstreams", [])
    except Exception as e:
        print(f"    BGPView upstreams: {e}")
    return []


def fetch_peers() -> list:
    """Peers IPv4 de AS28091 via BGPView."""
    url = f"https://api.bgpview.io/asn/{ASN}/peers"
    try:
        resp = requests.get(url, timeout=15, headers=HEADERS)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") == "ok":
            return data.get("data", {}).get("ipv4_peers", [])
    except Exception as e:
        print(f"    BGPView peers: {e}")
    return []


# ─── BUILD STATUS ─────────────────────────────────────────────────────────────

def build_status() -> dict:
    print("  [1/4] Consultando prefijos anunciados (RIPE STAT)...")
    announced = fetch_announced_prefixes()

    print("  [2/4] Consultando upstreams (BGPView)...")
    upstreams = fetch_upstreams()
    time.sleep(1)

    print("  [3/4] Consultando peers (BGPView)...")
    peers = fetch_peers()
    time.sleep(1)

    print("  [4/4] Verificando cada prefijo...")
    prefix_statuses = []
    for prefix in EXPECTED_PREFIXES:
        visible = prefix in announced
        state = "OK" if visible else "NO VISIBLE"
        print(f"    {prefix:22s}  {state}")

        detail = {}
        if visible:
            detail = fetch_prefix_detail(prefix)
            time.sleep(0.5)

        origin_asn = None
        for asn_info in detail.get("asns", []):
            origin_asn = asn_info.get("asn")
            break

        rir_name = "-"
        rir_alloc = detail.get("rir_allocation", {})
        if rir_alloc:
            rir_name = rir_alloc.get("rir_name", "-")

        prefix_statuses.append({
            "prefix":     prefix,
            "visible":    visible,
            "origin_asn": origin_asn,
            "rir":        rir_name,
        })

    return {
        "prefixes":        prefix_statuses,
        "upstreams":       upstreams,
        "peers":           peers,
        "announced_total": len(announced),
        "timestamp":       now_chile(),
    }


# ─── GRÁFICOS ─────────────────────────────────────────────────────────────────

def chart_prefix_donut(prefix_statuses: list) -> bytes:
    visible = sum(1 for p in prefix_statuses if p["visible"])
    missing = len(prefix_statuses) - visible

    labels, sizes, colors = [], [], []
    if visible:
        labels.append(f"Visibles ({visible})")
        sizes.append(visible)
        colors.append(C_GREEN)
    if missing:
        labels.append(f"No visibles ({missing})")
        sizes.append(missing)
        colors.append(C_RED)

    fig, ax = plt.subplots(figsize=(4.2, 3.2), facecolor="white")
    wedges, _ = ax.pie(
        sizes, labels=None, colors=colors,
        wedgeprops=dict(width=0.52, edgecolor="white", linewidth=2),
        startangle=90,
    )
    ax.text(0, 0.08, str(len(prefix_statuses)), ha="center", va="center",
            fontsize=22, fontweight="bold", color="#1e3a8a")
    ax.text(0, -0.22, "prefijos", ha="center", va="center",
            fontsize=8, color="#6b7280")
    ax.legend(wedges, labels, loc="lower center", bbox_to_anchor=(0.5, -0.18),
              ncol=2, fontsize=7.5, frameon=False)
    ax.set_title("Visibilidad de Prefijos BGP", fontsize=10, fontweight="bold",
                 color="#1e3a8a", pad=8)
    plt.tight_layout(pad=0.5)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def chart_upstreams_bars(upstreams: list) -> bytes:
    if not upstreams:
        return b""
    top = upstreams[:10]
    names = [f"AS{u.get('asn', '?')}  {u.get('name', '')[:22]}" for u in top]

    fig, ax = plt.subplots(figsize=(8.5, max(2.0, len(names) * 0.42)), facecolor="white")
    bars = ax.barh(names, [1] * len(names), color=C_BLUE,
                   edgecolor="white", linewidth=0.5, height=0.6)
    ax.set_xlim(0, 1.5)
    ax.set_xticks([])
    for bar, u in zip(bars, top):
        country = u.get("country_code", "")
        ax.text(bar.get_width() + 0.04,
                bar.get_y() + bar.get_height() / 2,
                country, va="center", fontsize=8, color="#374151")
    ax.set_title(f"Upstreams IPv4 de AS{ASN}", fontsize=10, fontweight="bold",
                 color="#1e3a8a", pad=8)
    ax.tick_params(axis="y", labelsize=7.5)
    for spine in ax.spines.values():
        spine.set_visible(False)
    plt.tight_layout(pad=0.8)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# ─── PDF ──────────────────────────────────────────────────────────────────────

def build_pdf(status: dict) -> str:
    now       = status["timestamp"]
    prefixes  = status["prefixes"]
    upstreams = status["upstreams"]
    peers     = status["peers"]

    visible_count = sum(1 for p in prefixes if p["visible"])
    missing_count = len(prefixes) - visible_count
    fecha_str = now.strftime("%d/%m/%Y %H:%M")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)

    # ════════════════════════════════════════════════════════
    # PÁGINA 1 — DASHBOARD
    # ════════════════════════════════════════════════════════
    pdf.add_page()

    pdf.set_fill_color(30, 58, 138)
    pdf.rect(0, 0, 210, 38, "F")
    pdf.set_fill_color(37, 99, 235)
    pdf.rect(0, 35, 210, 3, "F")

    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_xy(12, 7)
    pdf.cell(0, 10, f"Monitor BGP — AS{ASN}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "", 9)
    pdf.set_xy(12, 20)
    pdf.set_text_color(147, 197, 253)
    pdf.cell(0, 6, "AREA DE REDES  |  AUTOMATIZACION IA  |  SixManager",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(191, 219, 254)
    pdf.set_xy(12, 27)
    pdf.cell(186, 6, f"Generado: {fecha_str} (hora Chile)", align="R",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_text_color(0, 0, 0)
    pdf.set_y(46)

    # KPIs
    kpis = [
        ("PREFIJOS TOTALES", str(len(prefixes)),       (30,  58, 138), (219, 234, 254)),
        ("VISIBLES",         str(visible_count),       (22, 101,  52), (220, 252, 231)),
        ("NO VISIBLES",      str(missing_count),
            (153, 27, 27)  if missing_count else (100, 100, 100),
            (254, 226, 226) if missing_count else (243, 244, 246)),
        ("UPSTREAMS IPv4",   str(len(upstreams)),      (30,  58, 138), (219, 234, 254)),
        ("PEERS IPv4",       str(len(peers)),          (30,  58, 138), (219, 234, 254)),
    ]
    kw, kh, kgap = 36, 24, 2.5
    kx = (210 - (kw * 5 + kgap * 4)) / 2

    for i, (label, value, txt_color, bg_color) in enumerate(kpis):
        x = kx + i * (kw + kgap)
        y = pdf.get_y()
        pdf.set_fill_color(220, 220, 220)
        pdf.rect(x + 0.5, y + 0.5, kw, kh, "F")
        pdf.set_fill_color(*bg_color)
        pdf.rect(x, y, kw, kh, "F")
        pdf.set_fill_color(*txt_color)
        pdf.rect(x, y, kw, 2, "F")
        pdf.set_font("Helvetica", "B", 16)
        pdf.set_text_color(*txt_color)
        pdf.set_xy(x, y + 4)
        pdf.cell(kw, 9, value, align="C", new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.set_font("Helvetica", "B", 6)
        pdf.set_text_color(100, 100, 100)
        pdf.set_xy(x, y + 15)
        pdf.cell(kw, 5, label, align="C", new_x=XPos.RIGHT, new_y=YPos.TOP)

    pdf.set_y(pdf.get_y() + kh + 6)

    # Gráficos
    img_donut     = chart_prefix_donut(prefixes)
    img_upstreams = chart_upstreams_bars(upstreams)
    y_charts = pdf.get_y()
    donut_w  = 72

    pdf.image(io.BytesIO(img_donut), x=10, y=y_charts, w=donut_w)
    if img_upstreams:
        bars_w = 210 - donut_w - 16
        pdf.image(io.BytesIO(img_upstreams), x=donut_w + 14, y=y_charts, w=bars_w)

    pdf.set_y(y_charts + 58)

    # Banner de estado
    pdf.set_draw_color(226, 232, 240)
    pdf.set_line_width(0.5)
    pdf.line(10, pdf.get_y() + 2, 200, pdf.get_y() + 2)
    pdf.ln(6)

    note_y = pdf.get_y()
    if missing_count:
        missing_list = ", ".join(p["prefix"] for p in prefixes if not p["visible"])
        pdf.set_fill_color(254, 226, 226)
        pdf.set_draw_color(220, 38, 38)
        pdf.set_line_width(0.5)
        pdf.rect(10, note_y, 190, 10, "FD")
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(153, 27, 27)
        pdf.set_xy(12, note_y + 2)
        pdf.cell(0, 6, f"ALERTA: {missing_count} prefijo(s) no visible(s): {missing_list}")
        pdf.ln(16)
    else:
        pdf.set_fill_color(220, 252, 231)
        pdf.set_draw_color(34, 197, 94)
        pdf.set_line_width(0.3)
        pdf.rect(10, note_y, 190, 8, "FD")
        pdf.set_font("Helvetica", "I", 7.5)
        pdf.set_text_color(22, 101, 52)
        pdf.set_xy(12, note_y + 1.5)
        pdf.cell(0, 5, f"Todos los prefijos son visibles en la tabla de rutas global.  "
                       f"Total anunciados por AS{ASN}: {status['announced_total']}")
        pdf.ln(14)

    # ════════════════════════════════════════════════════════
    # PÁGINA 2 — DETALLE
    # ════════════════════════════════════════════════════════
    pdf.add_page()

    pdf.set_fill_color(30, 58, 138)
    pdf.rect(0, 0, 210, 14, "F")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(10, 4)
    pdf.cell(100, 6, "Detalle de Prefijos BGP", new_x=XPos.RIGHT, new_y=YPos.TOP)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(147, 197, 253)
    pdf.set_xy(110, 4)
    pdf.cell(90, 6, fecha_str, align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_text_color(0, 0, 0)
    pdf.set_y(20)

    # Tabla prefijos
    p_headers = ["Prefijo", "Tipo", "Estado", "ASN Origen", "RIR"]
    p_col_w   = [52, 22, 32, 36, 48]

    def draw_prefix_header():
        pdf.set_fill_color(30, 58, 138)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 8)
        for h, w in zip(p_headers, p_col_w):
            pdf.cell(w, 8, h, border=0, fill=True, align="C",
                     new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.ln()
        pdf.set_fill_color(37, 99, 235)
        pdf.rect(10, pdf.get_y(), sum(p_col_w), 0.8, "F")
        pdf.ln(1)

    draw_prefix_header()

    fill = False
    for p in prefixes:
        row_bg = (247, 250, 255) if fill else (255, 255, 255)
        bits   = int(p["prefix"].split("/")[1])
        ptype  = "Agregado" if bits <= 21 else f"/{bits}"
        origin = f"AS{p['origin_asn']}" if p.get("origin_asn") else "-"

        if p["visible"]:
            st_txt, st_color, st_bg = "Visible",    (22, 101, 52),  (220, 252, 231)
        else:
            st_txt, st_color, st_bg = "No visible", (153, 27, 27),  (254, 226, 226)

        pdf.set_fill_color(*row_bg)
        pdf.set_text_color(30, 41, 59)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(p_col_w[0], 7, p["prefix"], border=0, fill=True,
                 new_x=XPos.RIGHT, new_y=YPos.TOP)

        pdf.set_font("Helvetica", "", 8)
        pdf.cell(p_col_w[1], 7, ptype, border=0, fill=True, align="C",
                 new_x=XPos.RIGHT, new_y=YPos.TOP)

        pdf.set_fill_color(*st_bg)
        pdf.set_text_color(*st_color)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.cell(p_col_w[2], 7, st_txt, border=0, fill=True, align="C",
                 new_x=XPos.RIGHT, new_y=YPos.TOP)

        pdf.set_fill_color(*row_bg)
        pdf.set_text_color(30, 41, 59)
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(p_col_w[3], 7, origin, border=0, fill=True, align="C",
                 new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.cell(p_col_w[4], 7, p.get("rir", "-"), border=0, fill=True, align="C",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.set_draw_color(226, 232, 240)
        pdf.set_line_width(0.2)
        pdf.line(10, pdf.get_y(), 10 + sum(p_col_w), pdf.get_y())
        fill = not fill

    pdf.ln(10)

    # Tabla upstreams
    if upstreams:
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(30, 58, 138)
        pdf.cell(0, 6, f"Upstreams IPv4  ({len(upstreams)} encontrados)",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

        u_headers = ["ASN", "Nombre", "País", "Descripción"]
        u_col_w   = [24, 55, 18, 93]

        pdf.set_fill_color(30, 58, 138)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("Helvetica", "B", 8)
        for h, w in zip(u_headers, u_col_w):
            pdf.cell(w, 8, h, border=0, fill=True, align="C",
                     new_x=XPos.RIGHT, new_y=YPos.TOP)
        pdf.ln()
        pdf.set_fill_color(37, 99, 235)
        pdf.rect(10, pdf.get_y(), sum(u_col_w), 0.8, "F")
        pdf.ln(1)

        fill = False
        for u in upstreams[:20]:
            if pdf.get_y() > 265:
                break
            row_bg = (247, 250, 255) if fill else (255, 255, 255)
            pdf.set_fill_color(*row_bg)
            pdf.set_text_color(30, 41, 59)
            pdf.set_font("Helvetica", "", 7.5)
            pdf.cell(u_col_w[0], 6, f"AS{u.get('asn','')}", border=0, fill=True, align="C",
                     new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.cell(u_col_w[1], 6, str(u.get("name",""))[:32], border=0, fill=True,
                     new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.cell(u_col_w[2], 6, str(u.get("country_code","")), border=0, fill=True, align="C",
                     new_x=XPos.RIGHT, new_y=YPos.TOP)
            pdf.cell(u_col_w[3], 6, str(u.get("description",""))[:54], border=0, fill=True,
                     new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.set_draw_color(226, 232, 240)
            pdf.set_line_width(0.2)
            pdf.line(10, pdf.get_y(), 10 + sum(u_col_w), pdf.get_y())
            fill = not fill

    # Footer
    pdf.set_y(-16)
    pdf.set_fill_color(30, 58, 138)
    pdf.rect(0, pdf.get_y(), 210, 16, "F")
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(147, 197, 253)
    pdf.cell(0, 8,
             f"Monitor BGP generado automáticamente · Área de Redes SixManager · AS{ASN} · {now.strftime('%d/%m/%Y')}",
             align="C")

    pdf.output(PDF_FILE)
    print(f"[OK] PDF generado: {PDF_FILE}")
    return PDF_FILE


# ─── EMAIL ALERTA ─────────────────────────────────────────────────────────────

def send_alert(missing_prefixes: list):
    now       = now_chile()
    dia       = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"][now.weekday()]
    fecha_str = f"{dia} {now.strftime('%d/%m/%Y %H:%M')}"
    subject   = f"[ALERTA BGP] AS{ASN} — {len(missing_prefixes)} prefijo(s) no visible(s) — {fecha_str}"

    rows = "".join(
        f"<tr>"
        f"<td style='padding:9px 14px;font-family:monospace;font-size:14px;"
        f"color:#991b1b;border-bottom:1px solid #fecaca;'>{p}</td>"
        f"<td style='padding:9px 14px;text-align:center;border-bottom:1px solid #fecaca;'>"
        f"<span style='background:#dc2626;color:white;padding:3px 10px;border-radius:4px;"
        f"font-size:11px;font-weight:bold;'>NO VISIBLE</span></td></tr>"
        for p in missing_prefixes
    )

    html = f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:30px 0;">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0"
  style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.09);">
  <tr><td style="background:linear-gradient(135deg,#7f1d1d 0%,#dc2626 100%);padding:30px 40px 24px;">
    <p style="margin:0;color:#fca5a5;font-size:10px;letter-spacing:2.5px;text-transform:uppercase;font-weight:700;">
      ALERTA CRÍTICA — Área de Redes</p>
    <h1 style="margin:8px 0 4px;color:#fff;font-size:22px;font-weight:700;">Prefijos BGP No Visibles</h1>
    <p style="margin:0;color:#fecaca;font-size:13px;">AS{ASN} SIXMANAGER · {fecha_str}</p>
  </td></tr>
  <tr><td style="padding:32px 40px;">
    <p style="margin:0 0 20px;color:#374151;font-size:15px;line-height:1.7;">
      Se detectaron <strong style="color:#dc2626;">{len(missing_prefixes)} prefijo(s)</strong>
      que <strong>no son visibles</strong> en la tabla de rutas global para <strong>AS{ASN}</strong>.
    </p>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="border:1px solid #fecaca;border-radius:8px;overflow:hidden;margin-bottom:24px;">
      <thead><tr style="background:#fef2f2;">
        <th style="padding:10px 14px;text-align:left;font-size:11px;color:#7f1d1d;letter-spacing:1px;text-transform:uppercase;">Prefijo</th>
        <th style="padding:10px 14px;text-align:center;font-size:11px;color:#7f1d1d;letter-spacing:1px;text-transform:uppercase;">Estado</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#eff6ff;border-left:4px solid #2563eb;border-radius:0 8px 8px 0;margin-bottom:24px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:#1e40af;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Información</p>
        <p style="margin:0;color:#1e3a8a;font-size:13px;line-height:2.1;">
          📡 &nbsp;ASN: <strong>AS{ASN}</strong><br>
          🕐 &nbsp;Detectado: <strong>{fecha_str}</strong><br>
          🔍 &nbsp;Fuente: <strong>RIPE STAT / BGPView</strong>
        </p>
      </td></tr>
    </table>
    <p style="margin:0;color:#dc2626;font-size:13px;font-weight:600;">
      Verificar sesiones BGP con upstreams de manera inmediata.
    </p>
  </td></tr>
  <tr><td style="background:#f8fafc;border-top:1px solid #e5e7eb;padding:16px 40px;">
    <p style="margin:0;color:#9ca3af;font-size:11px;line-height:1.6;">
      🤖 &nbsp;Generado automáticamente por el
      <strong style="color:#6b7280;">Agente IA del Área de Redes</strong> · SixManager
    </p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    msg = MIMEText(html, "html", "utf-8")
    msg["From"]    = f"Agente IA Redes <{GMAIL_USER}>"
    msg["To"]      = EMAIL_TO
    msg["Subject"] = subject

    with smtplib.SMTP("smtp.gmail.com", 587) as srv:
        srv.ehlo(); srv.starttls()
        srv.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        srv.sendmail(GMAIL_USER, EMAIL_TO, msg.as_string())

    print(f"[OK] Alerta enviada → {EMAIL_TO}")


# ─── EMAIL REPORTE ────────────────────────────────────────────────────────────

def send_report(status: dict, pdf_path: str):
    now       = status["timestamp"]
    dia       = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"][now.weekday()]
    fecha_str = f"{dia} {now.strftime('%d/%m/%Y')}"
    subject   = f"[Agente IA | Redes] Reporte BGP AS{ASN} — {fecha_str}"

    prefixes      = status["prefixes"]
    visible_count = sum(1 for p in prefixes if p["visible"])
    missing_count = len(prefixes) - visible_count

    estado     = "Todo OK — Todos los prefijos son visibles" if not missing_count \
                 else f"ALERTA: {missing_count} prefijo(s) no visible(s)"
    col_estado = "#16a34a" if not missing_count else "#dc2626"
    bg_estado  = "#f0fdf4" if not missing_count else "#fef2f2"

    html = f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:30px 0;">
<tr><td align="center">
<table width="620" cellpadding="0" cellspacing="0"
  style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.09);">
  <tr><td style="background:linear-gradient(135deg,#1e3a8a 0%,#2563eb 100%);padding:30px 40px 24px;">
    <p style="margin:0;color:#93c5fd;font-size:10px;letter-spacing:2.5px;text-transform:uppercase;font-weight:700;">
      Área de Redes — Automatización IA</p>
    <h1 style="margin:8px 0 4px;color:#fff;font-size:22px;font-weight:700;">Reporte BGP — AS{ASN}</h1>
    <p style="margin:0;color:#bfdbfe;font-size:13px;">SixManager Tecnologías · {fecha_str}</p>
  </td></tr>
  <tr><td style="padding:32px 40px;">
    <div style="background:{bg_estado};border-left:4px solid {col_estado};
      border-radius:0 8px 8px 0;padding:12px 20px;margin-bottom:24px;">
      <p style="margin:0;color:{col_estado};font-size:14px;font-weight:700;">{estado}</p>
    </div>
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#eff6ff;border-left:4px solid #2563eb;border-radius:0 8px 8px 0;margin-bottom:24px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:#1e40af;font-size:11px;font-weight:700;letter-spacing:1px;text-transform:uppercase;">Resumen</p>
        <p style="margin:0;color:#1e3a8a;font-size:13px;line-height:2.1;">
          📡 &nbsp;ASN: <strong>AS{ASN}</strong><br>
          ✅ &nbsp;Prefijos visibles: <strong>{visible_count} / {len(prefixes)}</strong><br>
          🔗 &nbsp;Upstreams IPv4: <strong>{len(status['upstreams'])}</strong><br>
          👥 &nbsp;Peers IPv4: <strong>{len(status['peers'])}</strong><br>
          📅 &nbsp;Generado: <strong>{now.strftime('%d/%m/%Y %H:%M')} (hora Chile)</strong><br>
          📊 &nbsp;Envío automático: <strong>Diario 10:00 AM</strong>
        </p>
      </td></tr>
    </table>
    <p style="margin:0;color:#6b7280;font-size:13px;">Se adjunta el reporte completo en PDF.</p>
  </td></tr>
  <tr><td style="background:#f8fafc;border-top:1px solid #e5e7eb;padding:16px 40px;">
    <p style="margin:0;color:#9ca3af;font-size:11px;line-height:1.6;">
      🤖 &nbsp;Generado automáticamente por el
      <strong style="color:#6b7280;">Agente IA del Área de Redes</strong> · SixManager<br>
      Por favor no responder a este mensaje.
    </p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    outer = MIMEMultipart("mixed")
    outer["From"]    = f"Agente IA Redes <{GMAIL_USER}>"
    outer["To"]      = EMAIL_TO
    outer["Subject"] = subject

    outer.attach(MIMEMultipart("alternative"))
    outer.get_payload()[0].attach(MIMEText(html, "html", "utf-8"))

    with open(pdf_path, "rb") as f:
        pdf_part = MIMEApplication(f.read(), _subtype="pdf")
    pdf_part.add_header("Content-Disposition",
                        f'attachment; filename="BGP_AS{ASN}_{now.strftime("%Y%m%d_%H%M")}.pdf"')
    outer.attach(pdf_part)

    with smtplib.SMTP("smtp.gmail.com", 587) as srv:
        srv.ehlo(); srv.starttls()
        srv.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        srv.sendmail(GMAIL_USER, EMAIL_TO, outer.as_string())

    print(f"[OK] Reporte BGP enviado → {EMAIL_TO}")


# ─── EMAIL ESTADO OK ──────────────────────────────────────────────────────────

def send_ok_status(status: dict):
    now       = status["timestamp"]
    dia       = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"][now.weekday()]
    fecha_str = f"{dia} {now.strftime('%d/%m/%Y %H:%M')}"
    subject   = f"[BGP OK] AS{ASN} — Prefijos operativos — {fecha_str}"

    prefixes = status["prefixes"]
    rows = "".join(
        f"<tr>"
        f"<td style='padding:9px 14px;font-family:monospace;font-size:13px;"
        f"color:#166534;border-bottom:1px solid #bbf7d0;'>{p['prefix']}</td>"
        f"<td style='padding:9px 14px;text-align:center;border-bottom:1px solid #bbf7d0;'>"
        f"<span style='background:#16a34a;color:white;padding:3px 10px;border-radius:4px;"
        f"font-size:11px;font-weight:bold;'>OPERATIVO</span></td>"
        f"<td style='padding:9px 14px;text-align:center;color:#166534;"
        f"border-bottom:1px solid #bbf7d0;font-size:12px;font-weight:600;'>100%</td>"
        f"<td style='padding:9px 14px;text-align:center;color:#166534;"
        f"border-bottom:1px solid #bbf7d0;font-size:12px;'>✔ Alcanzable</td>"
        f"</tr>"
        for p in prefixes
    )

    html = f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#f1f5f9;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f1f5f9;padding:30px 0;">
<tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0"
  style="background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.09);">
  <tr><td style="background:linear-gradient(135deg,#14532d 0%,#16a34a 100%);padding:30px 40px 24px;">
    <p style="margin:0;color:#bbf7d0;font-size:10px;letter-spacing:2.5px;text-transform:uppercase;font-weight:700;">
      Estado BGP — Área de Redes SixManager</p>
    <h1 style="margin:8px 0 4px;color:#fff;font-size:22px;font-weight:700;">
      Prefijos BGP Operativos ✔</h1>
    <p style="margin:0;color:#dcfce7;font-size:13px;">AS{ASN} SIXMANAGER TECNOLOGIAS SPA · {fecha_str}</p>
  </td></tr>
  <tr><td style="padding:32px 40px;">

    <!-- Banner verde -->
    <div style="background:#f0fdf4;border-left:5px solid #16a34a;border-radius:0 8px 8px 0;
      padding:14px 20px;margin-bottom:28px;">
      <p style="margin:0;color:#14532d;font-size:15px;font-weight:700;">
        ✔ &nbsp;Todos los prefijos son visibles, alcanzables y funcionales</p>
      <p style="margin:0 0 0 24px;color:#166534;font-size:13px;margin-top:4px;">
        {len(prefixes)}/{len(prefixes)} prefijos visibles en la tabla de rutas global
      </p>
    </div>

    <!-- Tabla de prefijos -->
    <table width="100%" cellpadding="0" cellspacing="0"
      style="border:1px solid #bbf7d0;border-radius:8px;overflow:hidden;margin-bottom:28px;">
      <thead><tr style="background:#dcfce7;">
        <th style="padding:10px 14px;text-align:left;font-size:11px;color:#14532d;
          letter-spacing:1px;text-transform:uppercase;">Prefijo</th>
        <th style="padding:10px 14px;text-align:center;font-size:11px;color:#14532d;
          letter-spacing:1px;text-transform:uppercase;">Estado</th>
        <th style="padding:10px 14px;text-align:center;font-size:11px;color:#14532d;
          letter-spacing:1px;text-transform:uppercase;">Disponibilidad</th>
        <th style="padding:10px 14px;text-align:center;font-size:11px;color:#14532d;
          letter-spacing:1px;text-transform:uppercase;">Alcanzabilidad</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>

    <!-- Info técnica -->
    <table width="100%" cellpadding="0" cellspacing="0"
      style="background:#eff6ff;border-left:4px solid #2563eb;border-radius:0 8px 8px 0;margin-bottom:24px;">
      <tr><td style="padding:16px 20px;">
        <p style="margin:0 0 8px;color:#1e40af;font-size:11px;font-weight:700;
          letter-spacing:1px;text-transform:uppercase;">Detalle técnico</p>
        <p style="margin:0;color:#1e3a8a;font-size:13px;line-height:2.1;">
          📡 &nbsp;ASN: <strong>AS{ASN}</strong> — SIXMANAGER TECNOLOGIAS SPA<br>
          🔗 &nbsp;Upstreams IPv4 activos: <strong>{len(status['upstreams'])}</strong><br>
          👥 &nbsp;Peers IPv4: <strong>{len(status['peers'])}</strong><br>
          🌐 &nbsp;Total prefijos anunciados: <strong>{status['announced_total']}</strong><br>
          🕐 &nbsp;Verificado: <strong>{fecha_str} (hora Chile)</strong><br>
          🔍 &nbsp;Fuente: <strong>RIPE STAT / BGPView</strong>
        </p>
      </td></tr>
    </table>

  </td></tr>
  <tr><td style="background:#f8fafc;border-top:1px solid #e5e7eb;padding:16px 40px;">
    <p style="margin:0;color:#9ca3af;font-size:11px;line-height:1.6;">
      🤖 &nbsp;Verificación automática por el
      <strong style="color:#6b7280;">Agente IA del Área de Redes</strong> · SixManager<br>
      Monitoreo cada hora · Alerta inmediata ante cualquier anomalía BGP
    </p>
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    msg = MIMEText(html, "html", "utf-8")
    msg["From"]    = f"Agente IA Redes <{GMAIL_USER}>"
    msg["To"]      = EMAIL_TO
    msg["Subject"] = subject

    with smtplib.SMTP("smtp.gmail.com", 587) as srv:
        srv.ehlo(); srv.starttls()
        srv.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        srv.sendmail(GMAIL_USER, EMAIL_TO, msg.as_string())

    print(f"[OK] Estado OK enviado → {EMAIL_TO}")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"Iniciando monitoreo BGP — AS{ASN}...")
    status = build_status()

    prefixes      = status["prefixes"]
    visible_count = sum(1 for p in prefixes if p["visible"])
    missing       = [p["prefix"] for p in prefixes if not p["visible"]]

    print(f"\nResumen:")
    print(f"  Prefijos visibles : {visible_count}/{len(prefixes)}")
    print(f"  Upstreams IPv4    : {len(status['upstreams'])}")
    print(f"  Peers IPv4        : {len(status['peers'])}")
    print(f"  Anunciados totales: {status['announced_total']}")

    if missing:
        print(f"\n  ALERTA: {len(missing)} prefijo(s) caídos → {', '.join(missing)}")
        print("Enviando alerta por email...")
        send_alert(missing)
    else:
        print("  Estado: TODOS OK")
        if SEND_STATUS:
            print("Enviando confirmación de estado OK...")
            send_ok_status(status)

    if SEND_REPORT:
        print("\nGenerando reporte PDF...")
        pdf_path = build_pdf(status)
        print("Enviando reporte por email...")
        send_report(status, pdf_path)

    print("\n¡Listo!")
