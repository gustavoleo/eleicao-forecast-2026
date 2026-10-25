from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
import os
import time
import base64
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh
from src.presentation import CSS,scorecard,forecast_card,brazil_map,state_calls,compare_rounds

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
st.html(CSS)
st.html('<div class="brand"><span class="brand-title">eleições<span style="color:#65daa1">2026</span></span><small>PRESIDENTE · BRASIL</small></div>')

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
source_age = (now - datetime.fromisoformat(stats['generated_at'])).total_seconds() if stats else 0
stale = state.get('stale', True) or collector_age > 120
official_column,forecast_column=st.columns(2,gap='small')
with official_column:
    st.html(scorecard(state.get('official_candidates',[]),stats))
with forecast_column:
    st.html(forecast_card(state,stale=stale))
clock_label='Fonte sem atualização recente' if stale else 'Consultando o TSE'
st.html(f'<div class="source-clock"><span><span class="dot"></span>{clock_label}</span><span>Arquivo {brtime(stats.get("generated_at"))[11:19]} BRT</span></div>')
if stale:
    st.caption(f'O TSE ainda retorna o arquivo de {brtime(stats.get("generated_at"))}. Consulta do coletor: {brtime(state.get("checked_at",{}).get("br"))}.')
gate = state['gate']
if gate.get('goal_satisfied') and not stale:
    cand = next((c['name'] for c in state.get('official_candidates', []) if c['candidate_id'] == gate['candidate_id']), gate['candidate_id'])
    title = 'Eleito segundo o TSE' if gate['status'] == 'official_elected' else 'Resultado matematicamente assegurado nos dados publicados'
    st.success(f'{title}: {cand}. {gate["reason"]}')
else:
    st.caption('Nenhum presidente eleito confirmado por este painel.')
if gate.get('inequality'):
    with st.expander('Verificação matemática nacional'):
        st.caption('Teste conservador para vitória: ' + gate['inequality'] + ' — ' + ('satisfeito' if gate.get('goal_satisfied') else 'ainda não satisfeito'))
view=st.radio('Visualização',['Mapa','Previsão','Resultados'],horizontal=True,label_visibility='collapsed')
if view=='Mapa':
    show_partial=st.checkbox('Mostrar liderança parcial (ainda pode mudar)',value=False)
    secured_count=sum(call.get('status')=='mathematically_secured' for uf,call in state_calls(state).items() if uf!='zz')
    if secured_count==0 and not show_partial:
        st.info('Nenhum estado matematicamente definido ainda. O mapa está cinza porque os votos restantes ainda podem mudar a liderança; isso não é uma falha de carregamento.')
    else:
        st.caption(f'{secured_count} de 27 UFs com liderança matematicamente assegurada nos dados publicados.')
    st.html('<div class="map-legend"><span><i style="background:#f32d42"></i>13</span><span><i style="background:#4568e9"></i>22</span><span><i style="background:#555e4c"></i>Indefinido / sem dados recentes</span></div>')
    if show_partial:
        st.caption('Cor clara = líder na apuração parcial, ainda pode mudar. Cor forte = liderança matematicamente assegurada na UF.')
    encoded_map=base64.b64encode(brazil_map(state,show_partial=show_partial).encode()).decode()
    map_alt='Mapa do Brasil: liderança parcial em cores claras e definição matemática em cores fortes' if show_partial else 'Mapa do Brasil: apenas lideranças estaduais matematicamente asseguradas recebem cor'
    st.html(f'<img class="president-map" alt="{map_alt}" src="data:image/svg+xml;base64,{encoded_map}"/>')
    st.caption('Cor forte = liderança local que os votos restantes não podem reverter, condicionada aos dados publicados. Não equivale à eleição nacional. • = mesmo líder do 1º turno. Malha: IBGE.')
    regions={r['uf']:r for r in state.get('regions',[])}
    first={r['uf']:r for r in state.get('first_round',{}).get('regions',[])}
    options=['br']+sorted(regions)
    chosen=st.query_params.get('uf','br')
    chosen=chosen if chosen in options else 'br'
    selected=st.selectbox('Explorar apuração',options,index=options.index(chosen),format_func=lambda u:'Brasil' if u=='br' else ('Exterior' if u=='zz' else u.upper()))
    current=dict(stats,candidates=state.get('official_candidates',[])) if selected=='br' else regions[selected]
    comparison,first_leader=compare_rounds(current,first.get(selected))
    if selected!='br':
        st.html(scorecard(current.get('candidates',[]),current))
        call=state_calls(state).get(selected,{})
        st.caption('Liderança local matematicamente assegurada.' if call.get('candidate_id') else 'Votos restantes ainda podem alterar a liderança local, ou falta confirmação recente.')
    if comparison:
        st.caption('1º turno final × 2º turno parcial. Totais de fases diferentes não representam ganho/perda definitivo. “Votos iguais” compara os inteiros exatos.')
        st.dataframe(pd.DataFrame(comparison),hide_index=True,width='stretch')
    else:
        st.caption('Aguardando o arquivo oficial final do 1º turno para esta abrangência.')
if view=='Previsão':
    projection = state.get('projection')
    if state.get('errors', {}).get('projection'):
        st.warning('Não foi possível atualizar o modelo: dados incompatíveis. Qualquer projeção exibida conserva seu horário original.')
    primary=(projection or {}).get('primary_forecast',{})
    st.subheader('Previsão com AnoFox · DuckDB')
    st.caption(f'Motor preferencial: {primary.get("engine","anofox_forecast")} · versão {primary.get("extension_version","aguardando inicialização")}')
    municipal = state.get('municipalities',{})
    if municipal.get('enabled'):
        st.caption(f'Coleta municipal: {municipal.get("collected",0)}/{municipal.get("discovered",0)} localidades · ciclo planejado {municipal.get("planned_sweep_seconds",0)/60:.0f} min. BR: 15 s · UFs e painel: 60 s.')
    headline_ready=bool(projection and projection.get('headline_ready') and not stale)
    if not headline_ready:
        st.info('Previsão principal suspensa: fonte desatualizada.' if stale else primary.get('reason','Aguardando dados oficiais suficientes.'))
        st.write('Sem probabilidade de vitória publicada enquanto os critérios de qualidade não forem atendidos.')
        official=pd.DataFrame(state.get('official_candidates',[]))
        if not official.empty:
            tally=official[['name','votes','share']].rename(columns={'name':'Candidato','votes':'Votos apurados','share':'Participação nos válidos'})
            tally['Participação nos válidos']=tally['Participação nos válidos'].map(lambda x:f'{100*x:.2f}%')
            st.dataframe(tally,hide_index=True,width='stretch')
        with st.expander('Diagnóstico do modelo e comparação provisória'):
            st.json(primary)
            if projection:
                diagnostic=pd.DataFrame(projection['candidates'])[['name','observed_share','projected_share']]
                st.caption('Modelo geográfico auxiliar, sem validação eleitoral externa. Não constitui a previsão principal.')
                st.dataframe(diagnostic,hide_index=True)
    if headline_ready:
        candidates = projection['candidates']
        c1, c2, c3 = st.columns(3)
        first = candidates[0]
        c1.metric('Projeção · '+first['name'], f'{first["projected_share"]*100:.1f}%')
        c2.metric('Cenários favoráveis ao líder', prob(first['p_first_round'] if meta.get('round') == 1 else first['p_win']))
        c3.metric('Projeção · '+candidates[1]['name'], f'{candidates[1]["projected_share"]*100:.1f}%')
        st.caption('Frequência dos cenários simulados, não probabilidade eleitoral calibrada nem confirmação de vencedor.')
        if meta.get('round') == 1:
            c3.caption('P(ir ao segundo turno): ' + prob(candidates[1]['p_runoff']))
        st.caption(f'{projection["draws"]:,} simulações · atualização do modelo: {brtime(projection["ts"])} · {projection.get("uf_coverage", projection["geographic_units"])}/28 UFs e exterior · {projection.get("municipal_units",0)} municípios no modelo · α={projection["alpha_scale"]:g}')
        if not projection.get('geographic_complete'):
            st.warning('Cobertura geográfica incompleta: projeção provisória.')
        if projection.get('excluded_regions'):
            st.caption('UFs excluídas por defasagem ou erro: ' + ', '.join(k.upper() for k in projection['excluded_regions']))
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
            st.caption('A série também reflete alterações de cobertura, histórico e versão do modelo; uma oscilação não implica novos votos oficiais.')
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
            st.write('AnoFox usa erros emparelhados de validação para os cenários. O modelo geográfico auxiliar usa Dirichlet. Em ambos, votos já apurados permanecem fixos; frequências de cenários não significam certeza.')
            st.write('Arquivos de UF defasados são excluídos. O volume restante é reconciliado com o eleitorado nacional; a parcela sem cobertura usa a distribuição nacional e taxa válida de 75%.')
            st.write(projection['assumptions'])
            st.write('UFs substituídas por municípios reconciliados:', projection.get('refined_ufs',{}))
            st.write('UFs mantidas no nível estadual:',projection.get('municipal_fallbacks',{}))
with st.expander('Dados oficiais, cobertura e operação',expanded=view=='Resultados'):
    st.dataframe(pd.DataFrame(state.get('official_candidates', [])), hide_index=True)
    st.dataframe(pd.DataFrame(state.get('regions', [])), hide_index=True)
    st.json(state.get('errors', {}))
    st.write('Histórico:', state.get('history_status'))
    st.download_button('Baixar estado e evidências do painel', json.dumps(state, ensure_ascii=False, indent=2), file_name='eleicao-estado.json', mime='application/json')
st.divider()
st.caption(f'Fonte: TSE — Dados Abertos (resultados.tse.jus.br) · Projeção estatística, não é resultado oficial. · Backend: {state["backend"]} · req/s: {state["requests"]["current_rps"]} · atualização da tela: 60s')
st.link_button('Conferir no TSE', 'https://resultados.tse.jus.br/oficial/app/index.html')
