from fastapi import FastAPI
import requests

app = FastAPI(title="Monitor São Ludgero API")

LAT = -28.325
LON = -49.176
TIMEZONE = "America/Sao_Paulo"

@app.get("/")
def inicio():
    return {
        "status": "online",
        "sistema": "Monitor São Ludgero API"
    }

@app.get("/previsao-chuva")
def previsao_chuva(dias: int = 7):
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
    dados = resposta.json()

    return {
        "local": "São Ludgero/SC",
        "fonte": "Open-Meteo",
        "previsao_diaria": dados.get("daily", {}),
        "previsao_horaria": dados.get("hourly", {})
    }

@app.get("/alertas-ativos")
def alertas_ativos():
    return {
        "status": "em desenvolvimento",
        "mensagem": "Endpoint reservado para INMET, Defesa Civil SC e outros alertas."
    }

@app.get("/nivel-rio-braco-norte")
def nivel_rio_braco_norte():
    return {
        "status": "em desenvolvimento",
        "mensagem": "Endpoint reservado para dados da ANA."
    }

@app.get("/risco-hidrologico")
def risco_hidrologico():
    return {
        "risco": "baixo",
        "criterio": "Versão inicial baseada apenas na previsão de chuva.",
        "observacao": "Integração com ANA, INMET e CEMADEN será adicionada depois."
    }
