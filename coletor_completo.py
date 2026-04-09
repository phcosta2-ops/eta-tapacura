"""
=============================================================
COLETOR ETA TAPACURA - V4 (CALIBRACAO REFINADA)
=============================================================
Corrige a conversao pixel->valor para graficos onde os valores
ficam perto do fundo (Cor escala 0-250 com valor ~6,
Turbidez escala 0-20 com valor ~0.4).

O problema: em SVG, py=0 = topo (valor maximo), py=ph = fundo
(valor minimo). Mas o plot area tem padding/margem interna,
entao py nunca chega exatamente a 0 ou ph.

Solucao: usar os ticks do eixo Y (que tambem sao posicionados
em pixels) pra fazer uma calibracao precisa pixel<->valor.
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

    // Primeiro, coletar informacoes de TODOS os SVGs
    const svgInfos = [];
    svgs.forEach((svg, idx) => {
        const bbox = svg.getBoundingClientRect();
        svgInfos.push({ idx, bbox, svg });
    });

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

    // Coletar eixos Y com posicao PRECISA dos ticks em pixels
    // Os ticks do eixo Y sao linhas horizontais (gridlines) ou textos
    // posicionados em Y especificos dentro do SVG do eixo
    svgs.forEach((svg, idx) => {
        const bbox = svg.getBoundingClientRect();
        // Eixo Y: estreito e alto
        if (bbox.width > 50 || bbox.height < 80) return;

        const textos = svg.querySelectorAll('text');
        const vals = [];
        textos.forEach(t => {
            const v = parseFloat((t.textContent || '').trim().replace(',', '.'));
            if (!isNaN(v)) {
                // Posicao Y do texto DENTRO do SVG (usar transform ou posicao)
                const tb = t.getBoundingClientRect();

                // Posicao relativa ao SVG do eixo
                const relY = tb.y - bbox.y + tb.height / 2;

                // Tambem pegar a posicao absoluta pra mapear pro SVG do grafico
                vals.push({
                    val: v,
                    absY: tb.y + tb.height / 2,  // centro do texto na tela
                    relY: relY,
                    svgY: bbox.y
                });
            }
        });

        if (vals.length >= 2) {
            vals.sort((a, b) => a.absY - b.absY);  // topo pra baixo
            R.eixos.push({
                svg_idx: idx,
                bbox_x: bbox.x, bbox_y: bbox.y,
                bbox_w: bbox.width, bbox_h: bbox.height,
                valores: vals
            });
            R.debug.push('eixoY svg' + idx + ' y=' + bbox.y.toFixed(0) +
                ' h=' + bbox.height.toFixed(0) +
                ' vals=[' + vals.map(v => v.val + '@' + v.absY.toFixed(0)).join(', ') + ']');
        }
    });

    // Textos grandes (valores atuais)
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


def cor_match(cor1, cor2):
    """Compara duas cores RGB ignorando espacos."""
    return cor1.replace(" ", "") == cor2.replace(" ", "")


def cor_para_param(cor, mapa):
    """Busca parametro pela cor."""
    cor = cor.strip()
    if cor in mapa:
        return mapa[cor]
    for c, p in mapa.items():
        if cor_match(c, cor):
            return p
    return None


def coletar():
    log.info("=" * 60)
    log.info("COLETA V4 - ETA TAPACURA (CALIBRACAO REFINADA)")
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


def construir_mapa_pixel_valor(eixo, graf):
    """
    Constroi uma funcao de mapeamento pixel Y -> valor
    usando os ticks do eixo Y e a posicao do grafico na tela.

    Os ticks do eixo Y estao em coordenadas absolutas (tela).
    O grafico SVG tambem tem coordenadas absolutas (bbox).
    Precisamos converter os ticks pra coordenadas do viewBox do SVG.
    """
    vals = eixo["valores"]  # [{val, absY}, ...]
    if len(vals) < 2:
        return None

    # O grafico e o eixo compartilham a mesma faixa vertical na tela
    # absY do tick -> posicao relativa no grafico
    graf_top = graf["bbox_y"]       # Y absoluto do topo do SVG do grafico
    graf_bottom = graf["bbox_y"] + graf["bbox_h"]  # Y absoluto do fundo
    graf_h_screen = graf["bbox_h"]  # altura na tela

    # Proporcao tela -> viewBox
    vb_h = graf["ph"]  # altura do viewBox
    scale = vb_h / graf_h_screen if graf_h_screen > 0 else 1

    # Converter cada tick pra coordenada do viewBox
    ticks = []
    for v in vals:
        # Posicao do tick na tela -> posicao relativa no SVG
        rel_screen = v["absY"] - graf_top
        # Converter pra viewBox
        vb_y = rel_screen * scale
        ticks.append({"val": v["val"], "vb_y": vb_y})

    # Ordenar por vb_y (topo pra baixo = valor maior pra menor)
    ticks.sort(key=lambda t: t["vb_y"])

    # Precisamos de pelo menos 2 ticks pra interpolar
    if len(ticks) < 2:
        return None

    # Regressao linear: val = a * vb_y + b
    # Usar primeiro e ultimo tick
    t_top = ticks[0]      # menor vb_y = topo = maior valor
    t_bot = ticks[-1]     # maior vb_y = fundo = menor valor

    dy = t_bot["vb_y"] - t_top["vb_y"]
    dv = t_bot["val"] - t_top["val"]

    if abs(dy) < 0.001:
        return None

    # a = dv/dy (negativo: y cresce pra baixo, valor diminui)
    a = dv / dy
    b = t_top["val"] - a * t_top["vb_y"]

    return {"a": a, "b": b, "ticks": ticks}


def pixel_para_valor(py, mapa):
    """Converte coordenada Y do viewBox pra valor real."""
    return mapa["a"] * py + mapa["b"]


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

    # 2. Tempo: 8h
    tempo_fim = agora
    tempo_inicio = agora - timedelta(hours=8)
    duracao = 8 * 3600

    # 3. Associar cada grafico ao eixo Y mais proximo
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

        # Encontrar eixo Y adjacente (svg_idx mais proximo, antes do grafico)
        melhor_eixo = None
        melhor_dist = float('inf')
        for eixo in eixos:
            dist = graf["svg_idx"] - eixo["svg_idx"]
            # Eixo deve estar 1-3 SVGs antes do grafico
            if 0 < dist <= 3 and dist < melhor_dist:
                melhor_dist = dist
                melhor_eixo = eixo

        # Construir mapa de calibracao pixel->valor
        mapa_pv = None
        if melhor_eixo:
            mapa_pv = construir_mapa_pixel_valor(melhor_eixo, graf)
            if mapa_pv:
                # Validar com os ticks
                ticks = mapa_pv["ticks"]
                t0 = ticks[0]
                t1 = ticks[-1]
                log.info(f"  {param}: calibracao por ticks: "
                         f"py={t0['vb_y']:.1f}->val={t0['val']}, "
                         f"py={t1['vb_y']:.1f}->val={t1['val']}")

        # Converter coordenadas
        serie = []
        for coord in coords:
            # X -> tempo
            frac_x = coord["px"] / pw if pw > 0 else 0
            frac_x = max(0, min(1, frac_x))
            ts = tempo_inicio + timedelta(seconds=frac_x * duracao)

            # Y -> valor
            if mapa_pv:
                valor = pixel_para_valor(coord["py"], mapa_pv)
            else:
                # Fallback: escala simples
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

        # Validar com valor real
        valor_real = valores_reais.get(param)
        ultimo_calc = serie_unica[-1]["v"] if serie_unica else None
        erro = abs(ultimo_calc - valor_real) if (ultimo_calc is not None and valor_real is not None) else None

        uc_str = f"{ultimo_calc:.4f}" if ultimo_calc is not None else "N/A"
        er_str = f"{erro:.4f}" if erro is not None else "N/A"
        log.info(f"  {param}: {len(serie_unica)} pontos, "
                 f"ultimo_calc={uc_str}, real={valor_real}, erro={er_str}")

        # Se o erro ainda for grande, ajustar com offset simples
        if erro is not None and valor_real is not None and ultimo_calc is not None:
            if erro > 0.5 and valor_real > 0:
                # Calcular fator de correcao
                if abs(ultimo_calc) > 0.001:
                    fator = valor_real / ultimo_calc
                else:
                    # Valor calculado ~0 mas real nao: usar offset
                    offset = valor_real - ultimo_calc
                    log.info(f"  {param}: aplicando offset={offset:.4f}")
                    serie_unica = [{"t": d["t"], "v": round(d["v"] + offset, 4)} for d in serie_unica]
                    fator = None

                if fator is not None and 0.5 < fator < 5:
                    log.info(f"  {param}: aplicando fator={fator:.4f}")
                    serie_unica = [{"t": d["t"], "v": round(d["v"] * fator, 4)} for d in serie_unica]

                # Recalcular ultimo
                ultimo_final = serie_unica[-1]["v"] if serie_unica else None
                uf_str = f"{ultimo_final:.4f}" if ultimo_final is not None else "N/A"
                log.info(f"  {param}: apos correcao, ultimo={uf_str}")

        coleta["series"][param] = {
            "num_pontos": len(serie_unica),
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
