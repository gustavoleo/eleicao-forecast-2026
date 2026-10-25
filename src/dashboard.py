from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
import os
import time
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title='Brasil 2026 · Observatório eleitoral', page_icon='🗳️', layout='wide')
if os.getenv('AUTOREFRESH_MODE', 'native') == 'component':
    st_autorefresh(interval=60_000, key='minute_refresh')
else:
    # The optional iframe component can fail behind proxy challenge scripts.
    # Native fragment timers use the already-established Streamlit session.
    st.session_state.setdefault('_last_refresh', time.monotonic())
    @st.fragment(run_every=60)
    def native_refresh():
        if time.monotonic() - st.session_state['_last_refresh'] >= 55:
            st.session_state['_last_refresh'] = time.monotonic()
            st.rerun()
    native_refresh()
st.markdown('''<style>
.block-container{max-width:1200px;padding-top:2rem} h1{letter-spacing:-.04em}
[data-testid="stMetric"]{border:1px solid #71809644;border-radius:12px;padding:18px}
</style>''', unsafe_allow_html=True)
st.caption('OBSERVATÓRIO INDEPENDENTE / BRASIL 2026')
st.title('Cada voto conta.')
st.caption('Fonte: TSE — Dados Abertos (resultados.tse.jus.br)')
st.info('Projeção estatística, não é resultado oficial.')

# Community Cloud launches this file directly. The Podman service owns its own
# collector instead. One cached resource prevents per-visitor duplicate polling.
@st.cache_resource
def embedded_collector():
    import threading
    from src.ingest import Ingestor
    # Streamlit secrets are available as environment variables only at the root.
    collector = Ingestor()
    worker = threading.Thread(target=collector.run, daemon=True, name='embedded-collector')
    worker.start()
    return collector, worker

if os.getenv('RUN_EMBEDDED_INGESTOR') == '1':
    embedded_collector()

@st.cache_data(ttl=45)
def read_state():
    path = Path(os.getenv('DATA_DIR', 'data')) / 'public.json'
    if path.exists(): return json.loads(path.read_text(encoding='utf-8'))
    return None

def brtime(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo('America/Sao_Paulo')).strftime('%d/%m/%Y %H:%M:%S BRT') if value else '—'

def prob(value):
    if value is None: return 'Não se aplica'
    if value >= .9995: return '>99,9% (modelo)'
    if value < .0005: return '<0,1% (modelo)'
    return f'{100 * value:.1f}%'

state = read_state()
if not state:
    st.warning('Aguardando a primeira leitura válida do TSE. Nenhum resultado foi inferido.')
    st.stop()
now = datetime.now(timezone.utc)
collector_age = (now - datetime.fromisoformat(state['published_at'])).total_seconds()
meta = state.get('meta') or {}
stats = state.get('stats') or {}
st.subheader(meta.get('name', 'Descobrindo eleição no TSE'))
st.caption(f'Presidente · eleição {meta.get("codigo_eleicao", "—")} · {meta.get("round", "—")}º turno · painel: {brtime(state["published_at"])}')
source_age = (now - datetime.fromisoformat(stats['generated_at'])).total_seconds() if stats else 0
st.caption(f'Arquivo TSE gerado: {brtime(stats.get("generated_at"))} · última consulta BR: {brtime(state.get("checked_at", {}).get("br"))}')
stale = state.get('stale', True) or collector_age > 120
if stale:
    st.warning(f'Dados sem confirmação recente. Arquivo TSE há {source_age/60:.1f} min; coletor há {collector_age:.0f}s. A projeção abaixo é a última disponível.')
gate = state['gate']
if gate.get('goal_satisfied') and not stale:
    cand = next((c['name'] for c in state.get('official_candidates', []) if c['candidate_id'] == gate['candidate_id']), gate['candidate_id'])
    title = 'Eleito segundo o TSE' if gate['status'] == 'official_elected' else 'Resultado matematicamente assegurado nos dados publicados'
    st.success(f'{title}: {cand}. {gate["reason"]}')
else:
    st.warning('Nenhum vencedor confirmado por este painel.' if not stale else 'Confirmação de vencedor suspensa até nova leitura válida.')
if gate.get('inequality'):
    st.caption('Teste conservador para vitória: ' + gate['inequality'] + ' — ' + ('satisfeito' if gate.get('goal_satisfied') else 'ainda não satisfeito'))
st.progress(min(1., stats.get('apurado_pct', 0)/100), text=f'{stats.get("apurado_pct", 0):.3f}% das seções totalizadas')
projection = state.get('projection')
if projection:
    candidates = projection['candidates']
    c1, c2, c3 = st.columns(3)
    first = candidates[0]
    c1.metric('Líder da projeção · não é anúncio de eleito', first['name'], f'{first["projected_share"]*100:.2f}% projetado')
    c2.metric('P(vitória no 1º turno)' if meta.get('round') == 1 else 'P(vitória no 2º turno)', prob(first['p_first_round'] if meta.get('round') == 1 else first['p_win']))
    c3.metric('Segundo na projeção', candidates[1]['name'], f'{candidates[1]["projected_share"]*100:.2f}% projetado')
    if meta.get('round') == 1:
        c3.caption('P(ir ao segundo turno): ' + prob(candidates[1]['p_runoff']))
    st.caption(f'5.000 simulações · atualização do modelo: {brtime(projection["ts"])} · {projection["geographic_units"]}/28 UFs e exterior · α={projection["alpha_scale"]:g}')
    if not projection.get('geographic_complete'):
        st.warning('Cobertura geográfica incompleta: projeção provisória.')
    st.caption(projection['calibration'])
    if projection.get('historical_mapping'):
        st.caption('Hipótese histórica: mapeamento de números de urna de 2022. Não implica identidade entre pessoas/candidaturas.')
    df = pd.DataFrame(candidates)
    chart = go.Figure()
    chart.add_trace(go.Bar(name='Projeção', y=df.name, x=df.projected_share*100, orientation='h', marker_color='#29b6a4'))
    chart.add_trace(go.Scatter(name='Intervalo central de 90%', y=df.name,
        x=(df.ci_low+df.ci_high)*50, mode='markers', marker=dict(color='#e4b951',size=7),
        error_x=dict(type='data', symmetric=False, array=(df.ci_high-df.ci_low)*50, arrayminus=(df.ci_high-df.ci_low)*50)))
    chart.add_trace(go.Scatter(name='Apuração atual / baseline linear', y=df.name, x=df.naive_share*100, mode='markers',marker=dict(color='#ad95e8', size=11, symbol='diamond')))
    chart.update_layout(height=max(300, len(df)*60), xaxis_title='% dos votos válidos', xaxis_range=[0,100], yaxis=dict(autorange='reversed'), margin=dict(l=0,r=10,t=20,b=0), legend=dict(orientation='h'))
    st.plotly_chart(chart, width='stretch')
    timeline = pd.DataFrame(state.get('timeline', []))
    if not timeline.empty:
        timeline['ts'] = pd.to_datetime(timeline.ts)
        st.subheader('Evolução da projeção')
        st.line_chart(timeline.pivot_table(index='ts', columns='name', values='projected_share') * 100, y_label='% dos votos válidos')
    st.subheader('Cenários de segundo turno')
    if meta.get('round') == 2:
        st.write('A eleição já está no segundo turno. Novos emparelhamentos e vitória no primeiro turno não se aplicam.')
    else:
        st.caption('Probabilidade conjunta: nenhum candidato alcança maioria e este par ocupa as duas primeiras posições.')
        st.dataframe(pd.DataFrame([dict(Par=' × '.join(p['names']), Probabilidade=f'{p["probability"]*100:.2f}%',
            Condicional_ao_segundo_turno=prob(p['conditional_probability'])) for p in projection['pairings']]), hide_index=True)
    with st.expander('Números e hipóteses do modelo'):
        st.dataframe(df, hide_index=True)
        st.write('Volume válido restante estimado com cobertura do eleitorado, taxa observada (>5% das seções) ou hipótese de 75%. Seção não é unidade uniforme de votos.')
        st.write('A incerteza Dirichlet é aplicada apenas aos votos restantes. Votos já apurados permanecem fixos. Probabilidades extremas têm suavização de Jeffreys; não significam certeza.')
        st.write(projection['assumptions'])
else:
    st.info('Aguardando votos válidos para calcular uma projeção.')
with st.expander('Dados oficiais, cobertura e operação'):
    st.dataframe(pd.DataFrame(state.get('official_candidates', [])), hide_index=True)
    st.dataframe(pd.DataFrame(state.get('regions', [])), hide_index=True)
    st.json(state.get('errors', {}))
    st.write('Histórico:', state.get('history_status'))
    st.download_button('Baixar estado e evidências do painel', json.dumps(state, ensure_ascii=False, indent=2), file_name='eleicao-estado.json', mime='application/json')
st.divider()
st.caption(f'Fonte: TSE — Dados Abertos (resultados.tse.jus.br) · Projeção estatística, não é resultado oficial. · Backend: {state["backend"]} · req/s: {state["requests"]["current_rps"]} · atualização da tela: 60s')
st.link_button('Conferir no TSE', 'https://resultados.tse.jus.br/oficial/app/index.html')
