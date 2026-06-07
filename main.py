from fastapi import FastAPI
import requests
import re
import xml.etree.ElementTree as ET

app = FastAPI(title="Monitor São Ludgero API")

LAT = -28.325
LON = -49.176
TIMEZONE = "America/Sao_Paulo"

TERMOS_REGIAO_SC = [
    "Sul Catarinense",
    "Grande Florianópolis",
    "Vale do Itajaí",
    "Serrana",
    "Norte Catarinense",
    "Oeste Catarinense"
]


def buscar_previsao(dias: int = 7):
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": LAT,
        "longitude": LON,
        "daily": "precipitation_sum,temperature_2m_max,temperature_2m_min,wind_gusts_10m_max",
        "hourly": "precipitation,precipitation_probability",
        "timezone": TIMEZONE,
        "forecast_days": dias
    }

    resposta = requests.get(url, params=params, timeout=20)
    resposta.raise_for_status()
    return resposta.json()


def classificar_risco(acumulado_72h: float, acumulado_7d: float):
    if acumulado_72h >= 120 or acumulado_7d >= 180:
        return "muito alto", "Risco elevado para cheias, enxurradas e ocorrências generalizadas."
    elif acumulado_72h >= 80 or acumulado_7d >= 120:
        return "alto", "Atenção máxima para elevação de rios, enxurradas e alagamentos."
    elif acumulado_72h >= 50 or acumulado_7d >= 80:
        return "moderado", "Atenção para alagamentos pontuais e resposta rápida de córregos."
    elif acumulado_72h >= 25 or acumulado_7d >= 50:
        return "atenção", "Risco baixo a moderado, com atenção a pancadas localmente fortes."
    else:
        return "baixo", "Sem indicativo de risco hidrológico relevante pela previsão atual."


def texto_xml(elemento, nome):
    for item in elemento.iter():
        if item.tag.split("}")[-1] == nome:
            return item.text
    return None


def buscar_alertas_inmet():
    url_base = "https://apiprevmet3.inmet.gov.br/avisos/rss"
    resposta = requests.get(url_base, timeout=20)
    resposta.raise_for_status()

    texto = resposta.text
    ids = sorted(set(re.findall(r"avisos/(?:rss/)?(\d+)", texto)))

    alertas = []

    for aviso_id in ids[:100]:
        try:
            url_aviso = f"https://apiprevmet3.inmet.gov.br/avisos/rss/{aviso_id}"
            r = requests.get(url_aviso, timeout=15)

            if r.status_code != 200:
                continue

            raiz = ET.fromstring(r.content)

            headline = texto_xml(raiz, "headline")
            description = texto_xml(raiz, "description")
            severity = texto_xml(raiz, "severity")
            urgency = texto_xml(raiz, "urgency")
            certainty = texto_xml(raiz, "certainty")
            onset = texto_xml(raiz, "onset")
            expires = texto_xml(raiz, "expires")
            instruction = texto_xml(raiz, "instruction")
            area_desc = texto_xml(raiz, "areaDesc") or ""

            pertence_regiao = any(
                termo.lower() in area_desc.lower()
                for termo in TERMOS_REGIAO_SC
            )

            if pertence_regiao:
                alertas.append({
                    "id": aviso_id,
                    "titulo": headline,
                    "descricao": description,
                    "severidade": severity,
                    "urgencia": urgency,
                    "certeza": certainty,
                    "inicio": onset,
                    "fim": expires,
                    "areas_afetadas": area_desc,
                    "instrucoes": instruction,
                    "link": f"https://avisos.inmet.gov.br/{aviso_id}"
                })

        except Exception:
            continue

    return alertas


@app.get("/")
def inicio():
    return {
        "status": "online",
        "sistema": "Monitor São Ludgero API",
        "versao": "1.4"
    }


@app.get("/previsao-chuva")
def previsao_chuva(dias: int = 7):
    dados = buscar_previsao(dias)

    return {
        "local": "São Ludgero/SC",
        "fonte": "Open-Meteo",
        "previsao_diaria": dados.get("daily", {}),
        "previsao_horaria": dados.get("hourly", {})
    }


@app.get("/risco-hidrologico")
def risco_hidrologico():
    dados = buscar_previsao(7)
    diaria = dados.get("daily", {})
    chuva = diaria.get("precipitation_sum", [])

    chuva_24h = round(sum(chuva[:1]), 1)
    chuva_48h = round(sum(chuva[:2]), 1)
    chuva_72h = round(sum(chuva[:3]), 1)
    chuva_7d = round(sum(chuva), 1)

    risco, observacao = classificar_risco(chuva_72h, chuva_7d)

    return {
        "local": "São Ludgero/SC",
        "fonte": "Open-Meteo",
        "chuva_24h_mm": chuva_24h,
        "chuva_48h_mm": chuva_48h,
        "chuva_72h_mm": chuva_72h,
        "chuva_7dias_mm": chuva_7d,
        "risco": risco,
        "observacao": observacao,
        "criterio": {
            "baixo": "0 a 24 mm em 72h e menos de 50 mm em 7 dias",
            "atencao": "25 a 49 mm em 72h ou 50 a 79 mm em 7 dias",
            "moderado": "50 a 79 mm em 72h ou 80 a 119 mm em 7 dias",
            "alto": "80 a 119 mm em 72h ou 120 a 179 mm em 7 dias",
            "muito_alto": "120 mm ou mais em 72h ou 180 mm ou mais em 7 dias"
        }
    }


@app.get("/alertas-ativos")
def alertas_ativos():
    try:
        alertas = buscar_alertas_inmet()

        return {
            "local_referencia": "São Ludgero/SC",
            "fonte": "INMET - Alert-AS / CAP RSS",
            "quantidade_alertas_encontrados": len(alertas),
            "alertas": alertas,
            "observacao": "Filtro aplicado apenas sobre áreas afetadas em regiões de Santa Catarina."
        }

    except Exception as erro:
        return {
            "fonte": "INMET",
            "status": "erro_na_consulta",
            "mensagem": "Não foi possível consultar automaticamente os alertas do INMET neste momento.",
            "erro": str(erro),
            "consulta_manual": "https://alertas2.inmet.gov.br/"
        }


@app.get("/nivel-rio-braco-norte")
def nivel_rio_braco_norte():
    return {
        "status": "em desenvolvimento",
        "mensagem": "Endpoint reservado para dados da ANA."
    }
