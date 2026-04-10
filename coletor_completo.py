"""
=============================================================
COLETOR ETA TAPACURA - V5 (CALIBRACAO PRECISA)
=============================================================
Melhoria principal: usa regressao linear com TODOS os ticks
do eixo Y (least squares), nao so primeiro e ultimo.
Pra Cor e Turbidez que ficam perto do fundo do grafico,
aplica offset baseado no valor real mantendo a variacao
relativa da curva.

Cores GRAFICOS (trace-line):
  rgb(224,138,0)   = Cloro (ppm)
  rgb(255,240,0)   = Cor (uC)
  rgb(60,191,60)   = Turbidez (NTU)
  rgb(178,107,255) = pH

Cores TEXTOS (fontSize=80px):
  rgb(255,127,39)  = Cloro
  rgb(255,240,0)   = Cor
  rgb(34,177,76)   = Turbidez
  rgb(163,73,164)  = pH
=============================================================
"""

import json
import os
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger(__name__)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    log.error("Rode: pip install playwright && playwright install chromium")
    sys.exit(1)

# ============================================================
URL = "https://supervisorio.compesa.com.br/PIVision/#/Displays/704/ETA-TAPACURA--INSTRUMENTACAO-ANALITICA?hidetoolbar&hidesidebar"
USUARIO = os.environ.get("ETA_USUARIO", "SEU_USUARIO_AQUI")
SENHA = os.environ.get("ETA_SENHA", "SUA_SENHA_AQUI")

ARQUIVO_DADOS = "dados/dados_eta.json"
ARQUIVO_SCREENSHOT = "dados/ultimo_screenshot.png"
ARQUIVO_DEBUG = "dados/debug_html.txt"

CORES_GRAFICOS = {
    "rgb(224, 138, 0)": "cloro_ppm",
    "rgb(224,138,0)": "cloro_ppm",
    "rgb(255, 240, 0)": "cor_uc",
    "rgb(255,240,0)": "cor_uc",
    "rgb(60, 191, 60)": "turbidez_ntu",
    "rgb(60,191,60)": "turbidez_ntu",
    "rgb(178, 107, 255)": "ph",
    "rgb(178,107,255)": "ph",
}

CORES_TEXTOS = {
    "rgb(255, 127, 39)": "cloro_ppm",
    "rgb(255,127,39)": "cloro_ppm",
    "rgb(255, 240, 0)": "cor_uc",
    "rgb(255,240,0)": "cor_uc",
    "rgb(34, 177, 76)": "turbidez_ntu",
    "rgb(34,177,76)": "turbidez_ntu",
    "rgb(163, 73, 164)": "ph",
    "rgb(163,73,164)": "ph",
}

ESCALAS_FALLBACK = {
    "cloro_ppm": {"min": 0, "max": 6},
    "cor_uc": {"min": 0, "max": 250},
    "turbidez_ntu": {"min": 0, "max": 20},
    "ph": {"min": 0, "max": 7},
}
# ============================================================

JS_EXTRAIR = """
() => {
    const R = { graficos: [], eixos: [], textos: [], debug: [] };

    const svgs = document.querySelectorAll('svg');

    // Coletar graficos (trace-lines)
    svgs.forEach((svg, idx) => {
        const bbox = svg.getBoundingClientRect();
        if (bbox.width < 100 || bbox.height < 50) return;

        const traces = svg.querySelectorAll('path.trace-line');
        if (traces.length === 0) return;

        const viewBox = svg.getAttribute('viewBox') || '';
        const vb = viewBox.split(/[\\s,]+/).map(Number);

        traces.forEach(path => {
            const cor = window.getComputedStyle(path).stroke || '';
            const d = path.getAttribute('d') || '';
            if (d.length < 50) return;

            const coords = [];
            const matches = d.matchAll(/[ML]\\s*([\\d.eE+-]+)[,\\s]([\\d.eE+-]+)/gi);
            for (const m of matches) {
                coords.push({ px: parseFloat(m[1]), py: parseFloat(m[2]) });
            }

            if (coords.length > 5) {
                let pw = bbox.width, ph = bbox.height;
                if (vb.length === 4) { pw = vb[2]; ph = vb[3]; }

                R.graficos.push({
                    svg_idx: idx, cor: cor, n: coords.length,
                    coords: coords, pw: pw, ph: ph,
                    bbox_x: bbox.x, bbox_y: bbox.y,
                    bbox_w: bbox.width, bbox_h: bbox.height,
                    viewBox: viewBox
                });
                R.debug.push('trace svg' + idx + ' ' + cor + ' n=' + coords.length + ' ph=' + ph.toFixed(0));
            }
        });
    });

    // Coletar eixos Y - posicao PRECISA dos ticks
    svgs.forEach((svg, idx) => {
        const bbox = svg.getBoundingClientRect();
        if (bbox.width > 60 || bbox.height < 80) return;

        const textos = svg.querySelectorAll('text');
        const vals = [];
        textos.forEach(t => {
            const v = parseFloat((t.textContent || '').trim().replace(',', '.'));
            if (!isNaN(v)) {
                const tb = t.getBoundingClientRect();
                vals.push({
                    val: v,
                    absY: tb.y + tb.height / 2
                });
            }
        });

        if (vals.length >= 2) {
            vals.sort((a, b) => a.absY - b.absY);
            R.eixos.push({
                svg_idx: idx,
                bbox_y: bbox.y, bbox_h: bbox.height,
                valores: vals
            });
            R.debug.push('eixoY svg' + idx + ' y=' + bbox.y.toFixed(0) +
                ' h=' + bbox.height.toFixed(0) +
                ' n=' + vals.length +
                ' range=' + vals[0].val + '-' + vals[vals.length-1].val);
        }
    });

    // Textos grandes (fontSize >= 40px)
    const all = document.querySelectorAll('*');
    all.forEach(el => {
        if (el.children.length > 2) return;
        const text = (el.innerText || '').trim();
        if (!text || text.length > 15) return;
        const fs = parseFloat(window.getComputedStyle(el).fontSize);
        if (fs < 40) return;
        const match = text.match(/^[\\d]+[,.]?[\\d]*$/);
        if (!match) return;
        const valor = parseFloat(text.replace(',', '.'));
        if (isNaN(valor) || valor < 0 || valor > 1000) return;
        const rect = el.getBoundingClientRect();
        R.textos.push({
            texto: text, valor: valor,
            x: rect.x, y: rect.y,
            color: window.getComputedStyle(el).color,
            fontSize: fs
        });
    });

    R.debug.push('textos=' + R.textos.length);
    return R;
}
"""


def cor_match(c1, c2):
    return c1.replace(" ", "") == c2.replace(" ", "")


def cor_para_param(cor, mapa):
    cor = cor.strip()
    if cor in mapa:
        return mapa[cor]
    for c, p in mapa.items():
        if cor_match(c, cor):
            return p
    return None


def least_squares_fit(points):
    """
    Regressao linear por minimos quadrados.
    points = [(x, y), ...]
    Retorna (a, b) onde y = a*x + b
    """
    n = len(points)
    if n < 2:
        return None, None

    sx = sum(p[0] for p in points)
    sy = sum(p[1] for p in points)
    sxx = sum(p[0]**2 for p in points)
    sxy = sum(p[0]*p[1] for p in points)

    denom = n * sxx - sx * sx
    if abs(denom) < 1e-10:
        return None, None

    a = (n * sxy - sx * sy) / denom
    b = (sy - a * sx) / n

    return a, b


def construir_mapa_pixel_valor(eixo, graf):
    """
    Mapeia pixel Y (viewBox) -> valor usando TODOS os ticks do eixo Y
    com regressao por minimos quadrados.
    """
    vals = eixo["valores"]
    if len(vals) < 2:
        return None

    graf_top = graf["bbox_y"]
    graf_h_screen = graf["bbox_h"]
    vb_h = graf["ph"]

    if graf_h_screen <= 0:
        return None

    scale = vb_h / graf_h_screen

    # Converter ticks de coordenada absoluta pra viewBox do grafico
    points = []
    for v in vals:
        rel_screen = v["absY"] - graf_top
        vb_y = rel_screen * scale
        points.append((vb_y, v["val"]))

    # Least squares: val = a * vb_y + b
    a, b = least_squares_fit(points)
    if a is None:
        return None

    # Calcular erro medio dos ticks (pra log)
    erros = [abs(a * p[0] + b - p[1]) for p in points]
    erro_medio = sum(erros) / len(erros)

    return {"a": a, "b": b, "n_ticks": len(points), "erro_medio": erro_medio}


def pixel_para_valor(py, mapa):
    return mapa["a"] * py + mapa["b"]


def coletar():
    log.info("=" * 60)
    log.info("COLETA V5 - ETA TAPACURA (CALIBRACAO PRECISA)")
    log.info("=" * 60)

    Path("dados").mkdir(exist_ok=True)

    with sync_playwright() as p:
        log.info("Iniciando navegador...")
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage",
                  "--disable-gpu", "--ignore-certificate-errors"]
        )
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            ignore_https_errors=True,
            http_credentials={"username": USUARIO, "password": SENHA}
        )
        page = context.new_page()

        try:
            log.info("Acessando PI Vision...")
            page.goto(URL, timeout=60000, wait_until="networkidle")

            login_f = page.query_selector("input[type='text'], input[name='username']")
            pass_f = page.query_selector("input[type='password']")
            if login_f and pass_f:
                log.info("Fazendo login...")
                login_f.fill(USUARIO)
                pass_f.fill(SENHA)
                btn = page.query_selector("button[type='submit'], input[type='submit']")
                if btn:
                    btn.click()
                else:
                    pass_f.press("Enter")
                page.wait_for_timeout(8000)

            log.info("Aguardando graficos (18s)...")
            page.wait_for_timeout(18000)

            page.screenshot(path=ARQUIVO_SCREENSHOT, full_page=True)
            log.info("Screenshot salvo")

            log.info("Extraindo dados...")
            R = page.evaluate(JS_EXTRAIR)

            for msg in R.get('debug', []):
                log.info(f"  [JS] {msg}")

            coleta = processar(R)

            # Salvar debug (sem coords pra economizar)
            try:
                with open(ARQUIVO_DEBUG, "w", encoding="utf-8") as f:
                    debug_data = {
                        "graficos": [{
                            "svg_idx": g["svg_idx"], "cor": g["cor"],
                            "n": g["n"], "pw": g["pw"], "ph": g["ph"],
                            "bbox_y": g["bbox_y"], "bbox_h": g["bbox_h"]
                        } for g in R["graficos"]],
                        "eixos": R["eixos"],
                        "textos": R["textos"],
                        "debug": R["debug"]
                    }
                    json.dump(debug_data, f, indent=2, ensure_ascii=False)
            except:
                pass

            salvar(coleta)

        except Exception as e:
            log.error(f"ERRO: {e}")
            try:
                page.screenshot(path=ARQUIVO_SCREENSHOT, full_page=True)
            except:
                pass
            salvar({"timestamp": datetime.now().isoformat(), "tipo": "erro",
                    "erro": str(e), "series": {}})
        finally:
            browser.close()
            log.info("Navegador fechado.")

    log.info("COLETA FINALIZADA")


def processar(R):
    agora = datetime.now()
    coleta = {
        "timestamp": agora.isoformat(),
        "tipo": "completa",
        "valores_atuais": {},
        "series": {}
    }

    graficos = R.get("graficos", [])
    eixos = R.get("eixos", [])
    textos = R.get("textos", [])

    # 1. Valores reais dos textos grandes
    valores_reais = {}
    for t in textos:
        param = cor_para_param(t["color"], CORES_TEXTOS)
        if param:
            valores_reais[param] = t["valor"]
            coleta["valores_atuais"][param] = t["valor"]
            log.info(f"  Texto: {param} = {t['valor']}")

    # 2. Tempo: 8h (padrao PI Vision)
    tempo_fim = agora
    tempo_inicio = agora - timedelta(hours=8)
    duracao = 8 * 3600

    # 3. Processar cada grafico
    for graf in sorted(graficos, key=lambda g: g["svg_idx"]):
        cor = graf["cor"].strip()
        param = cor_para_param(cor, CORES_GRAFICOS)
        if not param:
            log.warning(f"  Cor nao mapeada: {cor}")
            continue

        coords = graf["coords"]
        pw = graf["pw"]
        ph = graf["ph"]
        if pw <= 0 or ph <= 0:
            continue

        # Encontrar eixo Y adjacente (1-3 SVGs antes)
        melhor_eixo = None
        melhor_dist = float('inf')
        for eixo in eixos:
            dist = graf["svg_idx"] - eixo["svg_idx"]
            if 0 < dist <= 3 and dist < melhor_dist:
                melhor_dist = dist
                melhor_eixo = eixo

        # === CALIBRACAO EM 2 ETAPAS ===
        #
        # Etapa 1: Calibracao pelos ticks do eixo Y (least squares)
        # Isso da uma boa estimativa, mas pode ter erro significativo
        # pra valores perto do fundo (Cor, Turbidez)
        #
        # Etapa 2: Se temos o valor real (texto grande), recalibrar
        # usando 2 ancoras no espaco viewBox:
        #   - Ancora 1: ultimo ponto do trace (px maximo) → valor real
        #   - Ancora 2: tick de valor 0 do eixo Y → valor 0
        # Isso cria um mapeamento perfeito pra toda a faixa de valores

        mapa_pv = None
        if melhor_eixo:
            mapa_pv = construir_mapa_pixel_valor(melhor_eixo, graf)
            if mapa_pv:
                log.info(f"  {param}: calibracao ticks: "
                         f"{mapa_pv['n_ticks']} ticks, "
                         f"erro_medio={mapa_pv['erro_medio']:.4f}")

        # Tentar recalibrar usando valor real + tick zero
        valor_real = valores_reais.get(param)
        mapa_recal = None

        if valor_real is not None and melhor_eixo and valor_real > 0.001:
            # Encontrar o tick de valor 0 no eixo
            tick_zero = None
            for v in melhor_eixo["valores"]:
                if v["val"] == 0:
                    tick_zero = v
                    break

            # Ultimo ponto do trace (maior px = mais recente)
            ultimo_pt = max(coords, key=lambda c: c["px"])

            if tick_zero is not None:
                # Converter posicoes pra viewBox
                graf_top = graf["bbox_y"]
                graf_h_screen = graf["bbox_h"]
                vb_h = graf["ph"]
                scale = vb_h / graf_h_screen if graf_h_screen > 0 else 1

                # Posicao do tick zero no viewBox
                zero_screen = tick_zero["absY"] - graf_top
                zero_vb = zero_screen * scale

                # Posicao do ultimo ponto (ja esta no viewBox)
                ultimo_vb = ultimo_pt["py"]

                # 2 pontos: (zero_vb, 0) e (ultimo_vb, valor_real)
                dy = ultimo_vb - zero_vb
                if abs(dy) > 0.5:
                    a_recal = (valor_real - 0) / dy
                    b_recal = 0 - a_recal * zero_vb
                    mapa_recal = {"a": a_recal, "b": b_recal}

                    # Validar: o valor no tick zero deve ser ~0
                    val_at_zero = a_recal * zero_vb + b_recal
                    val_at_ultimo = a_recal * ultimo_vb + b_recal

                    log.info(f"  {param}: RECALIBRADO com valor real: "
                             f"zero_vb={zero_vb:.1f}->val={val_at_zero:.2f}, "
                             f"ultimo_vb={ultimo_vb:.1f}->val={val_at_ultimo:.2f}")

        # Escolher melhor mapa
        mapa_final = mapa_recal if mapa_recal else mapa_pv

        # Converter coordenadas
        serie = []
        for coord in coords:
            frac_x = coord["px"] / pw if pw > 0 else 0
            frac_x = max(0, min(1, frac_x))
            ts = tempo_inicio + timedelta(seconds=frac_x * duracao)

            if mapa_final:
                valor = mapa_final["a"] * coord["py"] + mapa_final["b"]
            else:
                escala = ESCALAS_FALLBACK[param]
                frac_y = 1.0 - (coord["py"] / ph) if ph > 0 else 0
                frac_y = max(0, min(1, frac_y))
                valor = escala["min"] + frac_y * (escala["max"] - escala["min"])

            serie.append({
                "t": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                "v": round(valor, 4)
            })

        serie.sort(key=lambda d: d["t"])

        # Deduplica por minuto
        seen = set()
        serie_unica = []
        for d in serie:
            k = d["t"][:16]
            if k not in seen:
                seen.add(k)
                serie_unica.append(d)

        if not serie_unica:
            continue

        # Log final
        ultimo_calc = serie_unica[-1]["v"]
        erro = abs(ultimo_calc - valor_real) if valor_real is not None else None
        erro_str = f", erro={erro:.4f}" if erro is not None else ""
        log.info(f"  {param}: {len(serie_unica)} pts, "
                 f"ultimo={ultimo_calc:.4f}, real={valor_real}{erro_str}")

        # Sanity check: se ainda tem valores muito negativos, a calibracao falhou
        min_val = min(d["v"] for d in serie_unica)
        max_val = max(d["v"] for d in serie_unica)
        if min_val < -5:
            log.warning(f"  {param}: min={min_val:.2f} muito negativo, "
                       f"mantendo so valor atual")
            serie_unica = [{"t": agora.strftime("%Y-%m-%dT%H:%M:%S"),
                           "v": valor_real if valor_real else 0}]

        coleta["series"][param] = {
            "num_pontos": len(serie_unica),
            "cor_svg": cor,
            "dados": serie_unica
        }

    return coleta


def salvar(coleta):
    historico = []
    if os.path.exists(ARQUIVO_DADOS):
        try:
            with open(ARQUIVO_DADOS, "r", encoding="utf-8") as f:
                historico = json.load(f)
        except:
            historico = []

    historico.append(coleta)
    if len(historico) > 720:
        historico = historico[-720:]

    with open(ARQUIVO_DADOS, "w", encoding="utf-8") as f:
        json.dump(historico, f, indent=2, ensure_ascii=False)

    total = sum(s.get("num_pontos", 0) for s in coleta.get("series", {}).values())
    log.info(f"Salvo. Coletas: {len(historico)}, Pontos: {total}")


if __name__ == "__main__":
    coletar()
