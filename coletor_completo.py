"""
=============================================================
COLETOR ETA TAPACURA - V3 (CALIBRADO COM TEXTOS)
=============================================================
Usa os valores grandes da tela (fontSize=80px) como ancora
pra calibrar a conversao pixel->valor dos graficos SVG.

Cores dos GRAFICOS (trace-line):
  - rgb(224,138,0)   = Cloro Residual (ppm)
  - rgb(255,240,0)   = Cor (uC)
  - rgb(60,191,60)   = Turbidez (NTU)
  - rgb(178,107,255) = pH

Cores dos TEXTOS (valores grandes):
  - rgb(255,127,39)  = Cloro
  - rgb(255,240,0)   = Cor
  - rgb(34,177,76)   = Turbidez
  - rgb(163,73,164)  = pH
=============================================================
"""

import json
import os
import sys
import time
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
# CONFIGURACOES
# ============================================================
URL = "https://supervisorio.compesa.com.br/PIVision/#/Displays/704/ETA-TAPACURA--INSTRUMENTACAO-ANALITICA?hidetoolbar&hidesidebar"
USUARIO = os.environ.get("ETA_USUARIO", "SEU_USUARIO_AQUI")
SENHA = os.environ.get("ETA_SENHA", "SUA_SENHA_AQUI")

ARQUIVO_DADOS = "dados/dados_eta.json"
ARQUIVO_SCREENSHOT = "dados/ultimo_screenshot.png"
ARQUIVO_DEBUG = "dados/debug_html.txt"

# Mapeamento de cores SVG trace-line -> parametro
CORES_GRAFICOS = {
    "rgb(224, 138, 0)":   "cloro_ppm",
    "rgb(224,138,0)":     "cloro_ppm",
    "rgb(255, 240, 0)":   "cor_uc",
    "rgb(255,240,0)":     "cor_uc",
    "rgb(60, 191, 60)":   "turbidez_ntu",
    "rgb(60,191,60)":     "turbidez_ntu",
    "rgb(178, 107, 255)": "ph",
    "rgb(178,107,255)":   "ph",
}

# Mapeamento de cores dos textos grandes -> parametro
CORES_TEXTOS = {
    "rgb(255, 127, 39)":  "cloro_ppm",
    "rgb(255,127,39)":    "cloro_ppm",
    "rgb(255, 240, 0)":   "cor_uc",
    "rgb(255,240,0)":     "cor_uc",
    "rgb(34, 177, 76)":   "turbidez_ntu",
    "rgb(34,177,76)":     "turbidez_ntu",
    "rgb(163, 73, 164)":  "ph",
    "rgb(163,73,164)":    "ph",
}

# Escalas dos eixos Y (baseado na imagem do PI Vision)
# Usadas como fallback se nao conseguir calibrar
ESCALAS_FALLBACK = {
    "cloro_ppm":     {"min": 0, "max": 6},
    "cor_uc":        {"min": 0, "max": 250},
    "turbidez_ntu":  {"min": 0, "max": 20},
    "ph":            {"min": 0, "max": 7},
}
# ============================================================


JS_EXTRAIR = """
() => {
    const R = { graficos: [], eixos: [], textos: [], debug: [] };

    const svgs = document.querySelectorAll('svg');

    svgs.forEach((svg, idx) => {
        const bbox = svg.getBoundingClientRect();
        if (bbox.width < 100 || bbox.height < 50) return;

        // Buscar apenas trace-lines (dados reais)
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
                // Determinar dimensoes do plot
                let pw = bbox.width, ph = bbox.height;
                if (vb.length === 4) { pw = vb[2]; ph = vb[3]; }

                // Encontrar min/max Y dos pixels pra este grafico
                let minPy = Infinity, maxPy = -Infinity;
                coords.forEach(c => {
                    if (c.py < minPy) minPy = c.py;
                    if (c.py > maxPy) maxPy = c.py;
                });

                R.graficos.push({
                    svg_idx: idx,
                    cor: cor,
                    n: coords.length,
                    coords: coords,
                    pw: pw, ph: ph,
                    minPy: minPy, maxPy: maxPy,
                    viewBox: viewBox
                });
                R.debug.push('trace ' + cor + ' n=' + coords.length +
                    ' ph=' + ph.toFixed(0) + ' pyRange=' + minPy.toFixed(1) + '-' + maxPy.toFixed(1));
            }
        });

        // Eixo Y: pegar textos numericos DENTRO deste SVG e do SVG eixo adjacente
        // O eixo Y fica no SVG anterior (idx-1)
    });

    // Pegar TODOS os textos de eixo Y
    // Eles ficam em SVGs separados, adjacentes aos graficos
    svgs.forEach((svg, idx) => {
        const bbox = svg.getBoundingClientRect();
        // Eixos Y sao SVGs estreitos e altos (width < 50, height > 100)
        if (bbox.width > 50 || bbox.height < 80) return;

        const textos = svg.querySelectorAll('text');
        const vals = [];
        textos.forEach(t => {
            const v = parseFloat((t.textContent || '').trim().replace(',', '.'));
            if (!isNaN(v)) {
                const tb = t.getBoundingClientRect();
                vals.push({ val: v, y: tb.y, absY: tb.y });
            }
        });

        if (vals.length >= 2) {
            vals.sort((a, b) => a.y - b.y);  // topo pra baixo
            R.eixos.push({
                svg_idx: idx,
                bbox_y: bbox.y,
                bbox_h: bbox.height,
                valores: vals
            });
            R.debug.push('eixoY svg' + idx + ' vals=' + vals.map(v => v.val).join(','));
        }
    });

    // Eixo X: timestamps
    const eixoX = [];
    svgs.forEach((svg, idx) => {
        const bbox = svg.getBoundingClientRect();
        // Eixos X sao SVGs largos e baixos
        if (bbox.height > 30 || bbox.width < 200) return;

        const textos = svg.querySelectorAll('text');
        textos.forEach(t => {
            const txt = (t.textContent || '').trim();
            if (txt.match(/\\d{2}[:\\/]\\d{2}/)) {
                const tb = t.getBoundingClientRect();
                eixoX.push({ texto: txt, x: tb.x });
            }
        });
    });
    R.debug.push('eixoX timestamps=' + eixoX.length);

    // Textos grandes (valores atuais) - fontSize >= 40px
    const all = document.querySelectorAll('*');
    all.forEach(el => {
        if (el.children.length > 2) return;
        const text = (el.innerText || '').trim();
        if (!text || text.length > 15) return;

        const fs = parseFloat(window.getComputedStyle(el).fontSize);
        if (fs < 40) return;  // so textos grandes

        const match = text.match(/^[\\d]+[,.]?[\\d]*$/);
        if (!match) return;

        const valor = parseFloat(text.replace(',', '.'));
        if (isNaN(valor) || valor < 0 || valor > 1000) return;

        const rect = el.getBoundingClientRect();
        const color = window.getComputedStyle(el).color;

        R.textos.push({
            texto: text, valor: valor,
            x: rect.x, y: rect.y,
            color: color, fontSize: fs
        });
    });

    R.debug.push('textos grandes=' + R.textos.length);
    return R;
}
"""


def coletar():
    log.info("=" * 60)
    log.info("COLETA V3 - ETA TAPACURA")
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

            # Login se necessario
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

            log.info("Aguardando graficos (15s)...")
            page.wait_for_timeout(15000)

            page.screenshot(path=ARQUIVO_SCREENSHOT, full_page=True)
            log.info("Screenshot salvo")

            log.info("Extraindo dados...")
            R = page.evaluate(JS_EXTRAIR)

            for msg in R.get('debug', []):
                log.info(f"  [JS] {msg}")

            coleta = processar(R)

            try:
                with open(ARQUIVO_DEBUG, "w", encoding="utf-8") as f:
                    # Salvar sem coords pra economizar espaco
                    debug_data = {
                        "graficos": [{**g, "coords": f"[{g['n']} pontos]"} for g in R["graficos"]],
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

    # 1. Mapear textos grandes -> valores atuais por cor
    valores_reais = {}
    for t in textos:
        cor = t["color"].strip()
        param = CORES_TEXTOS.get(cor)
        if not param:
            for c, p in CORES_TEXTOS.items():
                if c.replace(" ", "") == cor.replace(" ", ""):
                    param = p
                    break
        if param:
            valores_reais[param] = t["valor"]
            coleta["valores_atuais"][param] = t["valor"]
            log.info(f"  Texto: {param} = {t['valor']} (cor={cor})")

    # 2. Range de tempo (8h padrao do PI Vision)
    tempo_fim = agora
    tempo_inicio = agora - timedelta(hours=8)
    duracao = 8 * 3600  # segundos

    # 3. Processar cada grafico
    # Ordenar graficos pela posicao Y na tela (cloro=topo, ph=base)
    graficos_ordenados = sorted(graficos, key=lambda g: g.get("svg_idx", 0))

    for graf in graficos_ordenados:
        cor = graf["cor"].strip()
        param = CORES_GRAFICOS.get(cor)
        if not param:
            for c, p in CORES_GRAFICOS.items():
                if c.replace(" ", "") == cor.replace(" ", ""):
                    param = p
                    break
        if not param:
            log.warning(f"  Cor grafico nao mapeada: {cor}")
            continue

        coords = graf["coords"]
        pw = graf["pw"]  # largura do plot em pixels/viewbox
        ph = graf["ph"]  # altura do plot em pixels/viewbox

        if pw <= 0 or ph <= 0:
            continue

        # Determinar escala Y
        escala = ESCALAS_FALLBACK[param]
        y_min = escala["min"]
        y_max = escala["max"]

        # Tentar calibrar com eixo Y adjacente
        # Encontrar eixo Y mais proximo deste grafico
        melhor_eixo = None
        melhor_dist = float('inf')
        for eixo in eixos:
            # Eixo Y fica logo antes do SVG do grafico
            dist = abs(eixo["svg_idx"] - graf["svg_idx"])
            if dist < melhor_dist and dist <= 2:
                melhor_dist = dist
                melhor_eixo = eixo

        if melhor_eixo and len(melhor_eixo["valores"]) >= 2:
            vals = [v["val"] for v in melhor_eixo["valores"]]
            y_min = min(vals)
            y_max = max(vals)
            log.info(f"  {param}: eixo Y do SVG = {y_min}-{y_max}")

        # CALIBRACAO COM VALOR REAL:
        # Se temos o valor real (texto grande) e sabemos que e o ultimo ponto
        # do grafico, podemos validar/ajustar a escala
        valor_real = valores_reais.get(param)
        if valor_real is not None:
            # Pegar o ultimo ponto (maior X) e seu Y em pixel
            ultimo = max(coords, key=lambda c: c["px"])
            # Calcular que valor o pixel daria com a escala atual
            frac_y = 1.0 - (ultimo["py"] / ph) if ph > 0 else 0
            frac_y = max(0, min(1, frac_y))
            valor_calculado = y_min + frac_y * (y_max - y_min)

            log.info(f"  {param}: ultimo pixel Y={ultimo['py']:.1f}, "
                     f"calculado={valor_calculado:.2f}, real={valor_real}")

            # Se a diferenca for grande, recalibrar a escala
            if abs(valor_calculado - valor_real) > 0.1:
                log.info(f"  {param}: RECALIBRANDO escala...")
                # Usar o range de pixels pra estimar a escala real
                # frac_y = (valor_real - y_min_real) / (y_max_real - y_min_real)
                # Sabemos frac_y e valor_real, assumimos y_min_real = 0
                # Entao: y_max_real = valor_real / frac_y (se frac_y > 0)
                if frac_y > 0.05:
                    y_max_novo = valor_real / frac_y
                    y_min = 0
                    y_max = y_max_novo
                    log.info(f"  {param}: escala recalibrada para 0-{y_max:.2f}")

        # Converter todos os pontos
        serie = []
        for coord in coords:
            # X -> tempo
            frac_x = coord["px"] / pw if pw > 0 else 0
            frac_x = max(0, min(1, frac_x))
            ts = tempo_inicio + timedelta(seconds=frac_x * duracao)

            # Y -> valor
            frac_y = 1.0 - (coord["py"] / ph) if ph > 0 else 0
            frac_y = max(0, min(1, frac_y))
            valor = y_min + frac_y * (y_max - y_min)

            serie.append({
                "t": ts.strftime("%Y-%m-%dT%H:%M:%S"),
                "v": round(valor, 4)
            })

        serie.sort(key=lambda d: d["t"])

        # Remover duplicatas por minuto
        seen = set()
        serie_unica = []
        for d in serie:
            k = d["t"][:16]
            if k not in seen:
                seen.add(k)
                serie_unica.append(d)

        coleta["series"][param] = {
            "num_pontos": len(serie_unica),
            "escala_y": {"min": round(y_min, 2), "max": round(y_max, 2)},
            "dados": serie_unica
        }

        ultimo_v = serie_unica[-1]["v"] if serie_unica else None
        log.info(f"  {param}: {len(serie_unica)} pontos, "
                 f"escala={y_min:.1f}-{y_max:.1f}, ultimo={ultimo_v}")

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
    log.info(f"Salvo. Coletas: {len(historico)}, Pontos nesta coleta: {total}")


if __name__ == "__main__":
    coletar()
