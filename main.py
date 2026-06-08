
import asyncio
import math
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin

import httpx
from fastapi import FastAPI, Query

API_VERSION = "1.10-cemaden-diagnostico"

app = FastAPI(
    title="Monitor São Ludgero API",
    version=API_VERSION,
    description="API de monitoramento hidrometeorológico para São Ludgero e região.",
)

SAO_LUDGERO_REF = {
    "municipio": "São Ludgero",
    "uf": "SC",
    "latitude": -28.3269,
    "longitude": -49.1764,
    "timezone": "America/Sao_Paulo",
}

HEADERS_PADRAO = {
    "User-Agent": "Monitor-Sao-Ludgero-API/1.10 (+https://monitor-sao-ludgero.onrender.com)",
    "Accept": "application/json,text/html,application/xhtml+xml,application/xml,text/xml;q=0.9,*/*;q=0.8",
}

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

INMET_RSS_URL = "https://apiprevmet3.inmet.gov.br/avisos/rss"
INMET_AVISO_RSS_BASE = "https://apiprevmet3.inmet.gov.br/avisos/rss/"
INMET_AVISOS_PUBLICO = "https://avisos.inmet.gov.br/"

CEMADEN_GRAPH_URL = "https://resources.cemaden.gov.br/graficos/interativo/grafico_CEMADEN.php"


# ============================================================
# Utilitários gerais
# ============================================================

def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def agora_sao_ludgero_naive() -> datetime:
    # São Ludgero usa UTC-03; evita depender do pacote tzdata no Render.
    return datetime.now(timezone(timedelta(hours=-3))).replace(tzinfo=None)


def iso_utc() -> str:
    return agora_utc().isoformat()


def normalizar_texto(texto: Any) -> str:
    if texto is None:
        return ""
    return re.sub(r"\s+", " ", str(texto)).strip()


def remover_acentos_basico(texto: str) -> str:
    mapa = str.maketrans(
        "ÁÀÂÃÄáàâãäÉÈÊËéèêëÍÌÎÏíìîïÓÒÔÕÖóòôõöÚÙÛÜúùûüÇç",
        "AAAAAaaaaaEEEEeeeeIIIIiiiiOOOOOoooooUUUUuuuuCc",
    )
    return texto.translate(mapa)


def float_ou_none(valor: Any) -> float | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or texto in {"-", "--", "null", "None", "NaN", "nan"}:
        return None
    texto = texto.replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


async def get_json(url: str, params: dict[str, Any] | None = None, timeout_s: float = 20.0) -> dict[str, Any]:
    timeout = httpx.Timeout(timeout_s, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=HEADERS_PADRAO) as client:
        resposta = await client.get(url, params=params)
        resposta.raise_for_status()
        return resposta.json()


async def get_text(url: str, params: dict[str, Any] | None = None, timeout_s: float = 20.0) -> tuple[int, str, dict[str, str], str]:
    timeout = httpx.Timeout(timeout_s, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=HEADERS_PADRAO) as client:
        resposta = await client.get(url, params=params)
        return resposta.status_code, resposta.text or "", dict(resposta.headers), str(resposta.url)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    raio_terra_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return raio_terra_km * c


# ============================================================
# Open-Meteo
# ============================================================

async def buscar_open_meteo(forecast_days: int = 7) -> dict[str, Any]:
    params = {
        "latitude": SAO_LUDGERO_REF["latitude"],
        "longitude": SAO_LUDGERO_REF["longitude"],
        "timezone": SAO_LUDGERO_REF["timezone"],
        "forecast_days": max(1, min(forecast_days, 16)),
        "current": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "apparent_temperature",
                "precipitation",
                "rain",
                "weather_code",
                "cloud_cover",
                "wind_speed_10m",
                "wind_gusts_10m",
            ]
        ),
        "hourly": ",".join(
            [
                "precipitation",
                "precipitation_probability",
                "rain",
                "showers",
                "wind_gusts_10m",
            ]
        ),
        "daily": ",".join(
            [
                "precipitation_sum",
                "precipitation_probability_max",
                "wind_gusts_10m_max",
            ]
        ),
    }
    return await get_json(OPEN_METEO_URL, params=params)


def acumular_chuva_prevista(dados: dict[str, Any], horas: int) -> float:
    hourly = dados.get("hourly") or {}
    tempos = hourly.get("time") or []
    precipitacoes = hourly.get("precipitation") or []

    if not tempos or not precipitacoes:
        return 0.0

    inicio = agora_sao_ludgero_naive()
    fim = inicio + timedelta(hours=horas)

    acumulado = 0.0
    for tempo_txt, chuva in zip(tempos, precipitacoes):
        try:
            tempo = datetime.fromisoformat(str(tempo_txt))
        except ValueError:
            continue

        if inicio <= tempo < fim:
            acumulado += float_ou_none(chuva) or 0.0

    return round(acumulado, 1)


def max_probabilidade_chuva(dados: dict[str, Any], horas: int = 24) -> int | None:
    hourly = dados.get("hourly") or {}
    tempos = hourly.get("time") or []
    probs = hourly.get("precipitation_probability") or []

    if not tempos or not probs:
        return None

    inicio = agora_sao_ludgero_naive()
    fim = inicio + timedelta(hours=horas)

    valores = []
    for tempo_txt, prob in zip(tempos, probs):
        try:
            tempo = datetime.fromisoformat(str(tempo_txt))
        except ValueError:
            continue

        if inicio <= tempo < fim and prob is not None:
            valores.append(int(prob))

    return max(valores) if valores else None


def classificar_risco_hidrologico(acum24: float, acum48: float, acum72: float, prob24: int | None) -> dict[str, Any]:
    score = 0
    motivos = []

    if acum24 >= 100:
        score += 5
        motivos.append("Chuva prevista em 24h >= 100 mm.")
    elif acum24 >= 70:
        score += 4
        motivos.append("Chuva prevista em 24h entre 70 e 100 mm.")
    elif acum24 >= 40:
        score += 3
        motivos.append("Chuva prevista em 24h entre 40 e 70 mm.")
    elif acum24 >= 20:
        score += 1
        motivos.append("Chuva prevista em 24h entre 20 e 40 mm.")

    if acum72 >= 180:
        score += 5
        motivos.append("Chuva prevista em 72h >= 180 mm.")
    elif acum72 >= 120:
        score += 4
        motivos.append("Chuva prevista em 72h entre 120 e 180 mm.")
    elif acum72 >= 80:
        score += 2
        motivos.append("Chuva prevista em 72h entre 80 e 120 mm.")

    if prob24 is not None and prob24 >= 80:
        score += 1
        motivos.append("Probabilidade horária de chuva nas próximas 24h >= 80%.")

    if score >= 8:
        nivel = "muito_alto"
        cor = "vermelho"
        recomendacao = "Acionar monitoramento operacional reforçado e avaliar avisos preventivos."
    elif score >= 5:
        nivel = "alto"
        cor = "laranja"
        recomendacao = "Manter atenção para alagamentos, enxurradas e elevação rápida de cursos d'água."
    elif score >= 3:
        nivel = "moderado"
        cor = "amarelo"
        recomendacao = "Acompanhar atualização da previsão e chuva observada."
    elif score >= 1:
        nivel = "baixo"
        cor = "verde"
        recomendacao = "Monitoramento de rotina."
    else:
        nivel = "muito_baixo"
        cor = "verde"
        recomendacao = "Sem indicativo hidrometeorológico relevante pela previsão atual."

    return {
        "nivel": nivel,
        "cor": cor,
        "score": score,
        "motivos": motivos,
        "recomendacao": recomendacao,
        "observacao": "Classificação automática baseada em chuva prevista; não substitui análise da Defesa Civil.",
    }


# ============================================================
# INMET Alert-AS CAP/RSS
# ============================================================

def parse_data_alerta(valor: str | None) -> datetime | None:
    texto = normalizar_texto(valor)
    if not texto:
        return None

    try:
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    try:
        dt = parsedate_to_datetime(texto)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def tag_sem_namespace(tag: str) -> str:
    return tag.split("}", 1)[-1].lower()


def texto_no_filho(elemento: ET.Element, nome: str) -> str | None:
    nome = nome.lower()
    for filho in elemento.iter():
        if tag_sem_namespace(filho.tag) == nome:
            return normalizar_texto(filho.text)
    return None


def filhos_por_nome(elemento: ET.Element, nome: str) -> list[ET.Element]:
    nome = nome.lower()
    return [f for f in elemento.iter() if tag_sem_namespace(f.tag) == nome]


def extrair_parametros_cap(info: ET.Element) -> dict[str, str]:
    parametros = {}
    for parametro in filhos_por_nome(info, "parameter"):
        value_name = texto_no_filho(parametro, "valueName")
        value = texto_no_filho(parametro, "value")
        if value_name:
            parametros[value_name] = value or ""
    return parametros


def texto_alerta_contem_sc(texto: str) -> bool:
    alvo = remover_acentos_basico(texto).upper()

    termos_sc = [
        "SANTA CATARINA",
        "/SC",
        " SC ",
        "SUL CATARINENSE",
        "NORTE CATARINENSE",
        "OESTE CATARINENSE",
        "GRANDE FLORIANOPOLIS",
        "VALE DO ITAJAI",
        "SERRANA",
        "PLANALTO NORTE",
        "LITORAL SUL",
        "LITORAL NORTE",
    ]

    return any(termo in alvo for termo in termos_sc)


def parse_cap_alert(alerta: ET.Element) -> dict[str, Any]:
    info = None
    for candidato in alerta:
        if tag_sem_namespace(candidato.tag) == "info":
            info = candidato
            break

    if info is None:
        info = alerta

    parametros = extrair_parametros_cap(info)
    area_descs = [normalizar_texto(texto_no_filho(area, "areaDesc")) for area in filhos_por_nome(info, "area")]
    area_descs = [a for a in area_descs if a]

    identifier = texto_no_filho(alerta, "identifier")
    sent = texto_no_filho(alerta, "sent")
    event = texto_no_filho(info, "event")
    headline = texto_no_filho(info, "headline")
    description = texto_no_filho(info, "description")
    instruction = texto_no_filho(info, "instruction")
    severity = texto_no_filho(info, "severity")
    urgency = texto_no_filho(info, "urgency")
    certainty = texto_no_filho(info, "certainty")
    effective = texto_no_filho(info, "effective")
    onset = texto_no_filho(info, "onset")
    expires = texto_no_filho(info, "expires")
    web = texto_no_filho(alerta, "web") or texto_no_filho(info, "web")

    expires_dt = parse_data_alerta(expires)

    id_num = None
    for item in [web or "", identifier or ""]:
        m = re.search(r"(\d{4,})", item)
        if m:
            id_num = m.group(1)
            break

    return {
        "id": id_num,
        "identifier": identifier,
        "evento": event,
        "headline": headline,
        "descricao": description,
        "instrucoes": instruction,
        "severidade": severity,
        "urgencia": urgency,
        "certeza": certainty,
        "inicio": onset or effective,
        "expira": expires,
        "expirado": bool(expires_dt and expires_dt <= agora_utc()),
        "enviado_em": sent,
        "areas": area_descs,
        "parametros": parametros,
        "link": web or (urljoin(INMET_AVISOS_PUBLICO, id_num) if id_num else None),
        "fonte": "INMET Alert-AS CAP/RSS",
    }


def parse_inmet_feed(texto_xml: str) -> tuple[list[dict[str, Any]], list[str]]:
    avisos: list[dict[str, Any]] = []
    ids: list[str] = []

    raiz = ET.fromstring(texto_xml)

    # Caso 1: o próprio retorno já é CAP.
    if tag_sem_namespace(raiz.tag) == "alert":
        avisos.append(parse_cap_alert(raiz))
        return avisos, ids

    # Caso 2: feed com vários CAP alerts dentro.
    for elemento in raiz.iter():
        if tag_sem_namespace(elemento.tag) == "alert":
            avisos.append(parse_cap_alert(elemento))

    # Caso 3: RSS/Atom com links para /avisos/rss/{id}.
    for elemento in raiz.iter():
        tag = tag_sem_namespace(elemento.tag)

        if tag == "link":
            href = elemento.attrib.get("href") or normalizar_texto(elemento.text)
            m = re.search(r"/(?:rss/)?(\d{4,})\b", href or "")
            if m:
                ids.append(m.group(1))

        if tag in {"item", "entry"}:
            texto_item = " ".join(normalizar_texto(e.text) for e in elemento.iter() if e.text)
            for m in re.finditer(r"(?:avisos\.inmet\.gov\.br/|/avisos/rss/)(\d{4,})", texto_item):
                ids.append(m.group(1))

    ids = list(dict.fromkeys(ids))
    return avisos, ids


async def buscar_cap_por_id(client: httpx.AsyncClient, aviso_id: str) -> dict[str, Any] | None:
    try:
        resposta = await client.get(urljoin(INMET_AVISO_RSS_BASE, aviso_id))
        if resposta.status_code != 200:
            return None
        if "limite de requisições" in resposta.text.lower():
            return None

        raiz = ET.fromstring(resposta.text)
        if tag_sem_namespace(raiz.tag) == "alert":
            return parse_cap_alert(raiz)

        for elemento in raiz.iter():
            if tag_sem_namespace(elemento.tag) == "alert":
                return parse_cap_alert(elemento)

    except Exception:
        return None

    return None


async def buscar_alertas_inmet_sc(max_detalhes: int = 25) -> dict[str, Any]:
    status_http, texto, headers, url_final = await get_text(INMET_RSS_URL, timeout_s=20.0)

    if "limite de requisições" in texto.lower():
        return {
            "ok": False,
            "status_http": status_http,
            "url": url_final,
            "erro": "INMET retornou limite de requisições.",
            "alertas": [],
        }

    try:
        avisos, ids = parse_inmet_feed(texto)
    except Exception as exc:
        return {
            "ok": False,
            "status_http": status_http,
            "url": url_final,
            "content_type": headers.get("content-type"),
            "erro": f"Falha ao interpretar RSS/CAP do INMET: {exc}",
            "amostra": texto[:500],
            "alertas": [],
        }

    if ids and len(avisos) == 0:
        timeout = httpx.Timeout(20.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=HEADERS_PADRAO) as client:
            tarefas = [buscar_cap_por_id(client, aviso_id) for aviso_id in ids[:max_detalhes]]
            resultados = await asyncio.gather(*tarefas)
            avisos = [r for r in resultados if r]

    ativos_sc = []
    for aviso in avisos:
        texto_aviso = " ".join(
            [
                str(aviso.get("headline") or ""),
                str(aviso.get("descricao") or ""),
                str(aviso.get("areas") or ""),
                str(aviso.get("parametros") or ""),
            ]
        )

        if aviso.get("expirado"):
            continue

        if texto_alerta_contem_sc(texto_aviso):
            ativos_sc.append(aviso)

    ordem_severidade = {"Extreme": 0, "Severe": 1, "Moderate": 2, "Minor": 3}
    ativos_sc.sort(key=lambda a: ordem_severidade.get(str(a.get("severidade")), 9))

    return {
        "ok": True,
        "status_http": status_http,
        "url": url_final,
        "content_type": headers.get("content-type"),
        "total_alertas_lidos": len(avisos),
        "total_alertas_sc_ativos": len(ativos_sc),
        "alertas": ativos_sc,
    }


# ============================================================
# CEMADEN
# ============================================================

CEMADEN_ESTACOES_CANDIDATAS = [
    {
        "idpcd": 8481,
        "uf": "SC",
        "cidade": "BRAÇO DO NORTE",
        "nome": "Centro",
        "latitude": -28.2750,
        "longitude": -49.1650,
        "prioridade": 1,
        "observacao": "Estação candidata mais próxima por município vizinho.",
    },
    {
        "idpcd": 8734,
        "uf": "SC",
        "cidade": "PEDRAS GRANDES",
        "nome": "Rio Tubarao",
        "latitude": -28.4330,
        "longitude": -49.1850,
        "prioridade": 2,
        "observacao": "Estação candidata na bacia regional.",
    },
    {
        "idpcd": 6972,
        "uf": "SC",
        "cidade": "ORLEANS",
        "nome": "Três Barras",
        "latitude": -28.3600,
        "longitude": -49.2900,
        "prioridade": 3,
        "observacao": "Estação de apoio regional.",
    },
    {
        "idpcd": 6971,
        "uf": "SC",
        "cidade": "ORLEANS",
        "nome": "Centro",
        "latitude": -28.3580,
        "longitude": -49.2920,
        "prioridade": 4,
        "observacao": "Estação de apoio regional.",
    },
    {
        "idpcd": 7400,
        "uf": "SC",
        "cidade": "GRÃO PARÁ",
        "nome": "Unidade Sanitária Central",
        "latitude": -28.1850,
        "longitude": -49.2150,
        "prioridade": 5,
        "observacao": "Estação de apoio regional.",
    },
    {
        "idpcd": 7002,
        "uf": "SC",
        "cidade": "SÃO MARTINHO",
        "nome": "Garagem da Prefeitura",
        "latitude": -28.1600,
        "longitude": -48.9850,
        "prioridade": 6,
        "observacao": "Estação de apoio regional.",
    },
    {
        "idpcd": 8510,
        "uf": "SC",
        "cidade": "TUBARÃO",
        "nome": "São Martinho",
        "latitude": -28.4700,
        "longitude": -49.0200,
        "prioridade": 7,
        "observacao": "Estação de apoio regional.",
    },
]


def texto_limpo_html(html: str) -> str:
    texto = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", html, flags=re.I)
    texto = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", texto, flags=re.I)
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = unescape(texto)
    texto = texto.replace("\xa0", " ")
    texto = re.sub(r"\s+", " ", texto)
    return texto.strip()


def parse_datahora_cemaden_utc(valor: str | None) -> dict[str, Any]:
    if not valor:
        return {"datahora_utc_iso": None, "idade_horas": None, "parse_ok": False}

    texto = valor.strip()

    for formato in ["%d/%m/%y %H:%M", "%d/%m/%Y %H:%M"]:
        try:
            dt = datetime.strptime(texto, formato).replace(tzinfo=timezone.utc)
            idade = (agora_utc() - dt).total_seconds() / 3600
            return {
                "datahora_utc_iso": dt.isoformat(),
                "idade_horas": round(idade, 2),
                "parse_ok": True,
            }
        except ValueError:
            pass

    m = re.search(
        r"(?P<data>\d{2}/\d{2}/\d{4})\s+(?P<hora>\d{1,2})h(?:\s*UTC)?",
        texto,
        flags=re.I,
    )
    if m:
        try:
            dt = datetime.strptime(
                f"{m.group('data')} {int(m.group('hora')):02d}:00",
                "%d/%m/%Y %H:%M",
            ).replace(tzinfo=timezone.utc)
            idade = (agora_utc() - dt).total_seconds() / 3600
            return {
                "datahora_utc_iso": dt.isoformat(),
                "idade_horas": round(idade, 2),
                "parse_ok": True,
            }
        except ValueError:
            pass

    return {"datahora_utc_iso": None, "idade_horas": None, "parse_ok": False}


def extrair_linha_acumulados_cemaden(texto: str, estacao: dict[str, Any]) -> dict[str, Any] | None:
    if not estacao.get("cidade") or not estacao.get("nome"):
        return None

    uf = re.escape(estacao["uf"])
    cidade = re.escape(estacao["cidade"])
    nome = re.escape(estacao["nome"])
    sep = r"\s*,?\s*"
    numero = r"(?:-|\d+(?:[.,]\d+)?)"

    padrao = (
        rf"\b{uf}{sep}{cidade}{sep}{nome}{sep}"
        rf"(?P<datahora>\d{{2}}/\d{{2}}/\d{{2,4}}\s+\d{{2}}:\d{{2}}){sep}"
        rf"(?P<ultimo>{numero}){sep}"
        rf"(?P<acc1>{numero}){sep}"
        rf"(?P<acc6>{numero}){sep}"
        rf"(?P<acc12>{numero}){sep}"
        rf"(?P<acc24>{numero}){sep}"
        rf"(?P<acc48>{numero}){sep}"
        rf"(?P<acc72>{numero}){sep}"
        rf"(?P<acc96>{numero})"
    )

    m = re.search(padrao, texto, flags=re.I)
    if not m:
        return None

    datahora = m.group("datahora")
    tempo = parse_datahora_cemaden_utc(datahora)

    return {
        "metodo_extracao": "linha_tabela_acumulados",
        "datahora_utc_texto": datahora,
        "datahora_utc_iso": tempo["datahora_utc_iso"],
        "idade_horas": tempo["idade_horas"],
        "chuva_observada_mm": float_ou_none(m.group("ultimo")),
        "acumulado_1h_mm": float_ou_none(m.group("acc1")),
        "acumulado_6h_mm": float_ou_none(m.group("acc6")),
        "acumulado_12h_mm": float_ou_none(m.group("acc12")),
        "acumulado_24h_mm": float_ou_none(m.group("acc24")),
        "acumulado_48h_mm": float_ou_none(m.group("acc48")),
        "acumulado_72h_mm": float_ou_none(m.group("acc72")),
        "acumulado_96h_mm": float_ou_none(m.group("acc96")),
    }


def extrair_metadados_grafico_cemaden(texto: str) -> dict[str, Any] | None:
    meta: dict[str, Any] = {"metodo_extracao": "metadados_grafico_fallback"}

    m = re.search(
        r"Estação:\s*(?P<nome>[^|()]+?)\s*\((?P<codigo>[A-Z0-9]+)\).*?"
        r"Município:\s*(?P<cidade>[^/|]+)\s*/\s*(?P<uf>[A-Z]{2})",
        texto,
        flags=re.I,
    )
    if m:
        meta["nome_estacao_detectado"] = m.group("nome").strip()
        meta["codigo_estacao_detectado"] = m.group("codigo").strip()
        meta["cidade_detectada"] = m.group("cidade").strip()
        meta["uf_detectada"] = m.group("uf").strip()

    m = re.search(
        r"Atualização:\s*(?P<datahora>\d{2}/\d{2}/\d{2,4}\s+\d{1,2}(?::\d{2}|h)(?:\s*UTC)?)",
        texto,
        flags=re.I,
    )
    if m:
        datahora = m.group("datahora").replace("h", ":00").replace(" UTC", "").strip()
        tempo = parse_datahora_cemaden_utc(datahora)
        meta["datahora_utc_texto"] = m.group("datahora").strip()
        meta["datahora_utc_iso"] = tempo["datahora_utc_iso"]
        meta["idade_horas"] = tempo["idade_horas"]

    m = re.search(r"Precipitação:\s*(?P<mm>\d+(?:[.,]\d+)?)\s*mm", texto, flags=re.I)
    if m:
        meta["chuva_observada_mm"] = float_ou_none(m.group("mm"))

    if len(meta) == 1:
        return None

    meta.setdefault("chuva_observada_mm", None)
    meta.setdefault("acumulado_1h_mm", None)
    meta.setdefault("acumulado_24h_mm", None)
    meta.setdefault("acumulado_72h_mm", None)
    return meta


def campos_cemaden_encontrados(html: str, texto: str) -> dict[str, bool]:
    base = html + " " + texto
    return {
        "tem_placeholder_angular": "{{ x.acc1hr }}" in html or "{{ x.uf }}" in html,
        "tem_campo_uf": bool(re.search(r"\bUF\b|x\.uf", base, flags=re.I)),
        "tem_campo_cidade": bool(re.search(r"\bCidade\b|x\.cidade", base, flags=re.I)),
        "tem_campo_nome": bool(re.search(r"\bNome\b|nomeestacao|x\.nomeestacao", base, flags=re.I)),
        "tem_datahora": bool(re.search(r"Data\s*\(Horário UTC\)|datahoraUltimovalor|\d{2}/\d{2}/\d{2,4}", base, flags=re.I)),
        "tem_ultimo": bool(re.search(r"\bÚltimo\b|ultimovalor", base, flags=re.I)),
        "tem_acc1h": bool(re.search(r"acc1hr|\b1\b", base, flags=re.I)),
        "tem_acc24h": bool(re.search(r"acc24hr|\b24\b", base, flags=re.I)),
        "tem_acc72h": bool(re.search(r"acc72hr|\b72\b", base, flags=re.I)),
        "tem_highcharts": "Highcharts" in html,
        "tem_cemaden": "CEMADEN" in base.upper(),
    }


def estacao_com_distancia(estacao: dict[str, Any]) -> dict[str, Any]:
    distancia = haversine_km(
        SAO_LUDGERO_REF["latitude"],
        SAO_LUDGERO_REF["longitude"],
        estacao["latitude"],
        estacao["longitude"],
    )
    saida = dict(estacao)
    saida["distancia_aproximada_km"] = round(distancia, 2)
    saida["criterio_distancia"] = "aproximado_por_municipio_ou_localidade"
    return saida


async def consultar_estacao_cemaden(
    client: httpx.AsyncClient,
    estacao: dict[str, Any],
    incluir_amostra: bool = False,
) -> dict[str, Any]:
    estacao_base = estacao_com_distancia(estacao)
    params = {
        "idpcd": str(estacao["idpcd"]),
        "menu": "periodo",
        "uf": estacao["uf"],
    }

    inicio = agora_utc()

    try:
        resposta = await client.get(CEMADEN_GRAPH_URL, params=params, headers=HEADERS_PADRAO)
        duracao_ms = int((agora_utc() - inicio).total_seconds() * 1000)

        html = resposta.text or ""
        texto = texto_limpo_html(html)

        dados = extrair_linha_acumulados_cemaden(texto, estacao)
        if dados is None:
            dados = extrair_metadados_grafico_cemaden(texto)

        campos = campos_cemaden_encontrados(html, texto)

        valores_principais = {}
        if dados:
            valores_principais = {
                "chuva_observada_mm": dados.get("chuva_observada_mm"),
                "acumulado_1h_mm": dados.get("acumulado_1h_mm"),
                "acumulado_24h_mm": dados.get("acumulado_24h_mm"),
                "acumulado_72h_mm": dados.get("acumulado_72h_mm"),
            }

        dados_validos = any(v is not None for v in valores_principais.values())

        resultado = {
            "ok_http": 200 <= resposta.status_code < 300,
            "status_http": resposta.status_code,
            "tempo_resposta_ms": duracao_ms,
            "url_consultada": str(resposta.url),
            "content_type": resposta.headers.get("content-type"),
            "tamanho_resposta": len(html),
            "fonte": "CEMADEN/resources/graficos/interativo/grafico_CEMADEN.php",
            "estacao": estacao_base,
            "campos_encontrados": campos,
            "dados_extraidos": dados,
            "dados_validos": dados_validos,
        }

        if incluir_amostra:
            resultado["amostra_texto_normalizada"] = texto[:1800]

        return resultado

    except Exception as exc:
        return {
            "ok_http": False,
            "status_http": None,
            "erro": str(exc),
            "fonte": "CEMADEN/resources/graficos/interativo/grafico_CEMADEN.php",
            "estacao": estacao_base,
            "dados_extraidos": None,
            "dados_validos": False,
        }


# ============================================================
# Endpoints principais
# ============================================================

@app.get("/")
async def root():
    return {
        "api": "Monitor São Ludgero API",
        "versao": API_VERSION,
        "status": "online",
        "referencia": SAO_LUDGERO_REF,
        "endpoints": [
            "/previsao-chuva",
            "/risco-hidrologico",
            "/alertas-ativos",
            "/debug-cemaden",
            "/chuva-cemaden",
            "/health",
        ],
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "versao": API_VERSION,
        "timestamp_utc": iso_utc(),
    }


@app.get("/previsao-chuva")
async def previsao_chuva(
    dias: int = Query(7, ge=1, le=16, description="Número de dias de previsão."),
):
    try:
        dados = await buscar_open_meteo(forecast_days=dias)

        daily = dados.get("daily") or {}
        previsao_diaria = []

        for i, data in enumerate(daily.get("time") or []):
            previsao_diaria.append(
                {
                    "data": data,
                    "chuva_prevista_mm": (daily.get("precipitation_sum") or [None])[i],
                    "probabilidade_maxima_chuva_pct": (daily.get("precipitation_probability_max") or [None])[i],
                    "rajada_maxima_vento_kmh": (daily.get("wind_gusts_10m_max") or [None])[i],
                }
            )

        return {
            "api": "Monitor São Ludgero API",
            "versao": API_VERSION,
            "fonte": "Open-Meteo",
            "referencia": SAO_LUDGERO_REF,
            "atualizado_em_utc": iso_utc(),
            "condicoes_atuais": dados.get("current"),
            "acumulado_previsto_24h_mm": acumular_chuva_prevista(dados, 24),
            "acumulado_previsto_48h_mm": acumular_chuva_prevista(dados, 48),
            "acumulado_previsto_72h_mm": acumular_chuva_prevista(dados, 72),
            "previsao_diaria": previsao_diaria,
        }

    except Exception as exc:
        return {
            "api": "Monitor São Ludgero API",
            "versao": API_VERSION,
            "fonte": "Open-Meteo",
            "status": "erro",
            "erro": str(exc),
            "referencia": SAO_LUDGERO_REF,
            "atualizado_em_utc": iso_utc(),
        }


@app.get("/risco-hidrologico")
async def risco_hidrologico():
    try:
        dados = await buscar_open_meteo(forecast_days=7)

        acum24 = acumular_chuva_prevista(dados, 24)
        acum48 = acumular_chuva_prevista(dados, 48)
        acum72 = acumular_chuva_prevista(dados, 72)
        prob24 = max_probabilidade_chuva(dados, 24)

        risco = classificar_risco_hidrologico(acum24, acum48, acum72, prob24)

        return {
            "api": "Monitor São Ludgero API",
            "versao": API_VERSION,
            "fonte": "Open-Meteo",
            "referencia": SAO_LUDGERO_REF,
            "atualizado_em_utc": iso_utc(),
            "risco": risco,
            "chuva_prevista": {
                "acumulado_24h_mm": acum24,
                "acumulado_48h_mm": acum48,
                "acumulado_72h_mm": acum72,
                "probabilidade_maxima_24h_pct": prob24,
            },
        }

    except Exception as exc:
        return {
            "api": "Monitor São Ludgero API",
            "versao": API_VERSION,
            "status": "erro",
            "erro": str(exc),
            "referencia": SAO_LUDGERO_REF,
            "atualizado_em_utc": iso_utc(),
        }


@app.get("/alertas-ativos")
async def alertas_ativos():
    try:
        resultado = await buscar_alertas_inmet_sc()

        return {
            "api": "Monitor São Ludgero API",
            "versao": API_VERSION,
            "fonte": "INMET Alert-AS CAP/RSS",
            "referencia": "Santa Catarina",
            "atualizado_em_utc": iso_utc(),
            "status_fonte": "ok" if resultado.get("ok") else "erro",
            "detalhe_fonte": {
                "status_http": resultado.get("status_http"),
                "url": resultado.get("url"),
                "content_type": resultado.get("content_type"),
                "erro": resultado.get("erro"),
            },
            "total_alertas_sc_ativos": len(resultado.get("alertas") or []),
            "alertas": resultado.get("alertas") or [],
        }

    except Exception as exc:
        return {
            "api": "Monitor São Ludgero API",
            "versao": API_VERSION,
            "fonte": "INMET Alert-AS CAP/RSS",
            "status": "erro",
            "erro": str(exc),
            "atualizado_em_utc": iso_utc(),
            "alertas": [],
        }


@app.get("/debug-cemaden")
async def debug_cemaden(
    idpcd: int = Query(8481, description="ID público da PCD no gráfico CEMADEN."),
    uf: str = Query("SC", min_length=2, max_length=2, description="UF da estação."),
):
    estacao_debug = {
        "idpcd": idpcd,
        "uf": uf.upper(),
        "cidade": "BRAÇO DO NORTE" if idpcd == 8481 else "",
        "nome": "Centro" if idpcd == 8481 else "",
        "latitude": SAO_LUDGERO_REF["latitude"],
        "longitude": SAO_LUDGERO_REF["longitude"],
        "prioridade": 0,
        "observacao": "Consulta manual de diagnóstico.",
    }

    timeout = httpx.Timeout(20.0, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=HEADERS_PADRAO) as client:
        resultado = await consultar_estacao_cemaden(
            client=client,
            estacao=estacao_debug,
            incluir_amostra=True,
        )

    return {
        "api": "Monitor São Ludgero API",
        "versao": API_VERSION,
        "modo": "diagnostico",
        "mensagem": (
            "Endpoint de diagnóstico. Verifique status_http, campos_encontrados "
            "e dados_extraidos antes de usar em rotina operacional."
        ),
        "resultado": resultado,
    }


@app.get("/chuva-cemaden")
async def chuva_cemaden(
    max_idade_horas: int = Query(
        6,
        ge=1,
        le=168,
        description="Idade máxima aceitável do dado, em horas.",
    ),
    aceitar_dado_antigo: bool = Query(
        False,
        description="Se true, retorna a estação com dado válido mesmo fora da janela de idade.",
    ),
):
    timeout = httpx.Timeout(20.0, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=HEADERS_PADRAO) as client:
        tarefas = [
            consultar_estacao_cemaden(client, estacao, incluir_amostra=False)
            for estacao in CEMADEN_ESTACOES_CANDIDATAS
        ]
        resultados = await asyncio.gather(*tarefas)

    resultados_ordenados = sorted(
        resultados,
        key=lambda item: item["estacao"]["distancia_aproximada_km"],
    )

    estacao_mais_proxima = resultados_ordenados[0] if resultados_ordenados else None

    com_dado_valido = [
        r for r in resultados_ordenados
        if r.get("dados_validos") and r.get("dados_extraidos")
    ]

    recentes = []
    for r in com_dado_valido:
        idade = r["dados_extraidos"].get("idade_horas")
        if idade is not None and idade <= max_idade_horas:
            recentes.append(r)

    estacao_utilizada = None
    status = "sem_dado_recente"

    if recentes:
        estacao_utilizada = recentes[0]
        status = "ok"
    elif aceitar_dado_antigo and com_dado_valido:
        estacao_utilizada = com_dado_valido[0]
        status = "ok_dado_antigo"

    dados = estacao_utilizada.get("dados_extraidos") if estacao_utilizada else None

    return {
        "api": "Monitor São Ludgero API",
        "versao": API_VERSION,
        "fonte": "CEMADEN - páginas públicas de gráficos de PCDs",
        "modo": "diagnostico",
        "status": status,
        "referencia": SAO_LUDGERO_REF,
        "janela_maxima_idade_horas": max_idade_horas,
        "estacao_mais_proxima": estacao_mais_proxima,
        "estacao_utilizada": estacao_utilizada,
        "chuva_observada_mm": dados.get("chuva_observada_mm") if dados else None,
        "acumulado_1h_mm": dados.get("acumulado_1h_mm") if dados else None,
        "acumulado_24h_mm": dados.get("acumulado_24h_mm") if dados else None,
        "acumulado_72h_mm": dados.get("acumulado_72h_mm") if dados else None,
        "datahora_utc": dados.get("datahora_utc_iso") if dados else None,
        "idade_horas": dados.get("idade_horas") if dados else None,
        "mensagem": (
            "Dado CEMADEN válido e dentro da janela operacional."
            if status == "ok"
            else "Nenhuma estação próxima retornou dado válido e recente dentro da janela definida."
        ),
        "estacoes_consultadas": resultados_ordenados,
    }


if __name__ == "__main__":
    import uvicorn

    porta = int(os.environ.get("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=porta)
