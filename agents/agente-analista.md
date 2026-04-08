# Agente Analista

Você recebe os dados dos agentes Câmara e Emendas e produz os rankings e métricas para o relatório.

## Inputs

- `data/camara-data.json` — deputados + CEAP 2024
- `data/emendas-data.json` — emendas PIX 2024 por parlamentar

## Métricas a calcular

### 1. Ranking geral de emendas (top 30)
- Parlamentar, partido, UF, total em R$, número de emendas

### 2. Concentração geográfica
Para cada parlamentar: `pct_home_state = emendas_para_proprio_estado / total_emendas`
- 100% = todo o dinheiro vai para o estado que o elegeu
- Marque os outliers: quem manda emenda para estados onde não foi eleito?

### 3. Índice de retorno (emendas / cota)
`indice_retorno = total_emendas_2024 / ceap_total_2024`
- Alto = direciona muito dinheiro público por real gasto da cota
- Baixo = gasta muito da cota, direciona pouco em emendas
- Só calcule para deputados federais (que têm CEAP)

### 4. Análise por partido
- Média de emendas por deputado/senador por partido
- Partido com maior concentração geográfica média
- Partido com maior % de emendas em educação

### 5. Top municípios beneficiados
- 20 municípios que mais receberam emendas em valor
- UF, total recebido, número de emendas

### 6. Alertas e curiosidades
- Parlamentar com 0 emendas registradas (só gasta cota, não direciona nada)
- Parlamentar com emendas concentradas 100% fora do próprio estado
- Maior emenda individual do ano

## Formato de saída (JSON)

```json
{
  "ranking_emendas": [...],
  "concentracao_geografica": [...],
  "indice_retorno": [...],
  "por_partido": {...},
  "top_municipios": [...],
  "alertas": {
    "sem_emendas": [...],
    "fora_estado": [...],
    "maior_emenda": {...}
  },
  "resumo": {
    "total_emendas_pix_2024": 0,
    "n_parlamentares_com_emenda": 0,
    "media_por_parlamentar": 0,
    "uf_mais_beneficiada": ""
  }
}
```

## Instruções

- Use o nome normalizado (maiúsculas sem acento) como chave de join entre os dois datasets
- Se um deputado não aparecer nas emendas, registre emendas como 0 (não omita)
- Salve em `data/analise.json`
