"""Mobile presidential scorecard and an IBGE-backed, arithmetic-only state map."""
import html
import json
from pathlib import Path
from datetime import datetime,timezone
import pandas as pd
from .project import local_plurality_gate
from .ingest import fresh_national

COLORS={'13':'#f32d42','22':'#4568e9'}
PARTIAL_COLORS={'13':'#98525b','22':'#536da1'}
UF_CODES=dict(zip('11 12 13 14 15 16 17 21 22 23 24 25 26 27 28 29 31 32 33 35 41 42 43 50 51 52 53'.split(),
                 'ro ac am rr pa ap to ma pi ce rn pb pe al se ba mg es rj sp pr sc rs ms mt go df'.split()))
LABELS={'ac':(-70,-9.3),'am':(-64,-4),'rr':(-61.6,2),'ap':(-51.8,1.6),'pa':(-53.5,-4.5),
        'ro':(-63,-11),'mt':(-56.8,-13),'to':(-48.6,-10),'ma':(-45,-5.2),'pi':(-42.8,-8),
        'ce':(-39.5,-5),'rn':(-36.5,-5.7),'pb':(-36.6,-7.1),'pe':(-38,-8.5),'al':(-36.7,-9.5),'se':(-37.4,-10.7),
        'ba':(-41.7,-13),'go':(-50,-16),'df':(-47.8,-15.8),'ms':(-54.5,-21),'mg':(-44.8,-19),
        'es':(-40.5,-19.5),'rj':(-42.7,-22),'sp':(-48.5,-22),'pr':(-51.5,-24.7),'sc':(-50,-27),'rs':(-53.5,-30)}

CSS='''<style>
:root{color-scheme:dark} .stApp{background:#10110f;color:#f2f2ec}
[data-testid="stHeader"]{background:transparent} .block-container{max-width:820px;padding:1.1rem 1rem 3rem}
.brand{display:flex;justify-content:space-between;align-items:center;font-size:18px;font-weight:750;letter-spacing:-.04em;padding:10px 0 18px}
.brand small{font-size:11px;letter-spacing:.1em;color:#9aa29b;font-weight:500}
.race{border:1px solid #30332d;border-radius:20px;padding:14px;background:#171914;line-height:1.2}
.race-kicker{text-align:center;font-size:12px;color:#b7beb3;margin-bottom:15px;text-transform:uppercase;letter-spacing:.12em}
.contenders{display:flex;justify-content:space-between;gap:8px;align-items:end;margin-bottom:12px}
.candidate{min-width:0;max-width:46%}.candidate.right{text-align:right}.candidate b{font-size:21px;line-height:1.1;display:block;overflow-wrap:anywhere}.brand-title{display:flex;gap:0;align-items:baseline}
.candidate small{color:#a9b2a3;font-size:12px}.vote-bar{height:45px;display:flex;position:relative;border-radius:8px;overflow:hidden}
.vote-bar:after{content:"";position:absolute;left:50%;top:0;bottom:0;width:1px;background:#fff8}
.vote-bar div{display:flex;align-items:center;padding:0 10px;white-space:nowrap;font-size:22px;font-weight:750;box-sizing:border-box}
.vote-bar div:last-child{justify-content:end}.vote-bar span{font-size:11px;font-weight:450;opacity:.85;margin:0 5px}
.count-note{color:#a8b1a1;font-size:12px;margin-top:12px}.count-note b{color:#58d9a0;font-size:15px}
.forecast-title{font-size:16px;line-height:1.35;margin:0 0 4px;font-weight:700}.forecast-brand{font-size:11px;color:#65daa1;margin-bottom:16px}.forecast-row{margin:12px 0}.forecast-label{display:flex;justify-content:space-between;gap:8px;font-size:12px;margin-bottom:6px}.forecast-track{height:15px;border-radius:5px;background:#30342d;overflow:hidden}.forecast-track div{height:100%;border-radius:5px}.forecast-note{font-size:11px;color:#b7beb3;line-height:1.45;margin-top:10px}.forecast-pending{font-size:12px;color:#e4c77a;margin:12px 0}
.scenario-title{font-size:11px;color:#bbc3b5;margin-top:14px}.scenario-grid{display:grid;grid-template-columns:1.25fr 1fr 1fr;gap:5px 8px;font-size:10px;margin-top:6px}.scenario-grid b{color:#cbd2c6;font-weight:600}.scenario-grid span{font-variant-numeric:tabular-nums}.scenario-muted{color:#9ca597;font-size:10px;margin-top:6px}
.source-clock{display:flex;align-items:center;justify-content:space-between;gap:10px;border:1px solid #30332d;border-radius:30px;padding:12px 16px;color:#b6bfb0;font-size:12px;margin-top:12px}
.source-clock .dot{display:inline-block;width:7px;height:7px;border-radius:50%;background:#ecb64d;margin-right:7px}
.map-legend{display:flex;gap:15px;flex-wrap:wrap;color:#bbc3b5;font-size:11px;margin:8px 0}.map-legend i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}
.president-map{width:100%;max-height:530px;display:block}.president-map path{transition:fill .15s,stroke .15s;stroke:#10110f;stroke-width:1.7}.president-map a:hover path{stroke:#eee;stroke-width:2.5}
.president-map text{font:600 12px system-ui;fill:#c7cec2;pointer-events:none}.map-caption{font-size:11px;color:#8c9685;text-align:center}
button[data-baseweb="tab"]{font-size:14px;padding:10px 18px} [data-testid="stMetric"]{border:1px solid #30332d;border-radius:14px;padding:14px}
@media(max-width:540px){.block-container{padding:.5rem .8rem 2rem}.brand{font-size:17px}.race{padding:14px;border-radius:16px}.candidate b{font-size:19px}.vote-bar{height:42px}.vote-bar div{font-size:21px;padding:0 8px}.vote-bar span{display:none}.president-map{max-height:435px}.source-clock{font-size:11px;padding:11px 13px}}
</style>'''

def percent(x): return f'{100*x:.1f}'.replace('.',',')+'%'
def compact(n):
    return (f'{n/1e6:.1f}'.replace('.',',')+' milhões') if n>=1e6 else ((f'{n/1000:.0f} mil') if n>=1000 else str(n))

def scorecard(candidates,stats):
    eligible=[c for c in candidates if c.get('destination','').casefold() in ('válido','valido','')]
    eligible.sort(key=lambda c:(c['candidate_id']!='13',-c['votes']))
    if len(eligible)<2: return ''
    a,b=eligible[:2]; total=stats.get('valid_votes',0)
    sa=a['votes']/total if total else 0; sb=b['votes']/total if total else 0
    width=sa/(sa+sb)*100 if sa+sb else 50
    early='Ainda é cedo' if stats.get('apurado_pct',0)<1 else 'Votos oficiais'
    return f'''<div class="race"><div class="race-kicker">Presidente · {stats.get('round',2)}º turno</div>
    <div class="contenders"><div class="candidate"><b>{html.escape(a['name'])}</b><small>{html.escape(a.get('party',''))} · {html.escape(a['candidate_id'])}</small></div>
    <div class="candidate right"><b>{html.escape(b['name'])}</b><small>{html.escape(b.get('party',''))} · {html.escape(b['candidate_id'])}</small></div></div>
    <div class="vote-bar"><div style="width:{width:.4f}%;background:{COLORS.get(a['candidate_id'],'#a67cce')}">{percent(sa)}<span>{compact(a['votes'])}</span></div><div style="width:{100-width:.4f}%;background:{COLORS.get(b['candidate_id'],'#5b95ba')}"><span>{compact(b['votes'])}</span>{percent(sb)}</div></div>
    <div class="count-note"><b>{stats.get('apurado_pct',0):.2f}%</b> das seções totalizadas · {early}</div></div>'''


def forecast_card(state,stale=False):
    projection=state.get('projection') or {}
    primary=projection.get('primary_forecast') or {}
    model_error=bool(state.get('errors',{}).get('projection'))
    ready=bool(projection.get('headline_ready') and primary.get('status')=='ready' and not stale and not model_error)
    candidates=projection.get('candidates',[]) if ready else state.get('official_candidates',[])
    candidates=sorted(candidates,key=lambda c:(c['candidate_id']!='13',c['candidate_id']))
    forecast_rows=[]
    for c in candidates:
        value=percent(c['projected_share']) if ready else '—'
        fill=f'<div style="width:{100*c["projected_share"]:.4f}%;background:{COLORS.get(c["candidate_id"],"#9b78c6")}"></div>' if ready else ''
        interval=f'<div class="forecast-note">Intervalo do modelo: {percent(c["ci_low"])}–{percent(c["ci_high"])}</div>' if ready and len(candidates)!=2 else ''
        forecast_rows.append(f'<div class="forecast-row"><div class="forecast-label"><span>{html.escape(c["name"])}</span><b>{value}</b></div><div class="forecast-track">{fill}</div>{interval}</div>')
    status='Cenários estatísticos; não confirmam vencedor.' if ready else ('Fonte desatualizada; previsão suspensa.' if stale else 'Aguardando validação da previsão AnoFox.')
    if model_error: status='Atualização do modelo indisponível; previsão suspensa.'
    reason='' if ready else html.escape(primary.get('reason','Aguardando dados oficiais suficientes.'))
    scenarios=''
    if ready and len(candidates)==2:
        leader,runner=sorted(candidates,key=lambda c:-c['projected_share'])
        names=[html.escape(c['name']) for c in (leader,runner)]
        values=[(leader['ci_low'],runner['ci_high']),
                (leader['projected_share'],runner['projected_share']),
                (leader['ci_high'],runner['ci_low'])]
        scenario_rows=''.join('<span>'+label+'</span><span>'+f'{a*100:.2f}'.replace('.',',')+'%</span><span>'+f'{b*100:.2f}'.replace('.',',')+'%</span>'
                     for label,(a,b) in zip(['Limite inferior','Estimativa central','Limite superior'],values))
        scenarios=(f'<div class="scenario-title">Cenários AnoFox · limites do intervalo de 90% · {int(projection.get("draws",0)):,} simulações</div>'
                   f'<div class="scenario-grid"><b>Cenário</b><b>{names[0]}</b><b>{names[1]}</b>{scenario_rows}</div>'
                   '<div class="scenario-muted">Extremos da faixa modelada; cenários condicionais, sem garantia de resultado.</div>')
    backend=state.get('backend','DuckDB')
    backend_note='Armazenamento: Hardcharger / ADBC' if 'Hardcharger' in backend else 'Armazenamento: DuckDB · Hardcharger não conectado'
    return ('<div class="race"><div class="forecast-title">Previsão com AnoFox e Hardcharger</div>'
            '<div class="forecast-brand">by Gizmo Data</div>'+''.join(forecast_rows)+
            f'<div class="forecast-pending">{status}</div><div class="forecast-note">{reason}</div>'
            +scenarios+f'<div class="forecast-note">{backend_note}</div></div>')

def state_calls(state,now=None):
    now=now or datetime.now(timezone.utc)
    calls={}
    for region in state.get('regions',[]):
        candidates=region.get('candidates',[])
        if not candidates: continue
        fresh=fresh_national(region,region.get('checked_at') or state['published_at'],now,region['uf'] in state.get('errors',{}))
        calls[region['uf']]=local_plurality_gate(pd.DataFrame(candidates),region,fresh)
    return calls

def compare_rounds(current,first):
    if not first or not first.get('final') or not first.get('official'):
        return [],None
    previous={c['candidate_id']:c for c in first['candidates']}
    rows=[]
    for c in current.get('candidates',[]):
        old=previous.get(c['candidate_id'])
        if old and old.get('full_name','').casefold()==c.get('full_name','').casefold():
            rows.append({'Candidato':c['name'],'1º turno':int(old['votes']),'2º turno parcial':int(c['votes']),
                         'Votos iguais':int(old['votes'])==int(c['votes']),
                         'Variação (p.p.)':round((c['share']-old['share'])*100,2)})
    ranked=sorted(first['candidates'],key=lambda c:-c['votes'])
    leader=None
    if len(ranked)>1 and ranked[0]['votes']>ranked[1]['votes']:
        old_leader=ranked[0]
        same_person=any(c['candidate_id']==old_leader['candidate_id'] and
                        bool(c.get('full_name')) and
                        c['full_name'].casefold()==old_leader.get('full_name','').casefold()
                        for c in current.get('candidates',[]))
        if same_person: leader=old_leader['candidate_id']
    return rows,leader

def brazil_map(state,show_partial=False):
    geo=json.loads((Path(__file__).parent/'assets/br-ufs.geojson').read_text())
    calls=state_calls(state)
    first={r['uf']:r for r in state.get('first_round',{}).get('regions',[])}
    regions={r['uf']:r for r in state.get('regions',[])}
    def xy(pair): return ((pair[0]+74)*10.5+12,(6-pair[1])*11.1+5)
    shapes=[]; labels=[]
    narrow='rn pb pe al se es rj'.split()
    for feature in geo['features']:
        uf=UF_CODES[str(feature['properties']['codarea'])]
        geometry=feature['geometry']; polygons=geometry['coordinates'] if geometry['type']=='MultiPolygon' else [geometry['coordinates']]
        path=' '.join('M'+' L'.join(f'{x:.1f},{y:.1f}' for x,y in map(xy,ring))+' Z' for polygon in polygons for ring in polygon)
        call=calls.get(uf,{})
        secure=call.get('status')=='mathematically_secured'
        color=COLORS.get(call.get('candidate_id'),'#9b78c6') if secure else '#30342d'
        _,previous=compare_rounds(regions.get(uf,{}),first.get(uf))
        same=secure and previous==call.get('candidate_id')
        title=uf.upper()+(' · liderança local matematicamente assegurada' if secure else ' · resultado local ainda indefinido')
        region=regions.get(uf)
        if show_partial and not secure and region and region.get('official') and region.get('cargo')=='1':
            fresh=fresh_national(region,region.get('checked_at') or state['published_at'],datetime.now(timezone.utc),uf in state.get('errors',{}))
            eligible=sorted((c for c in region.get('candidates',[]) if c.get('destination','').casefold() in ('válido','valido','')),key=lambda c:-c['votes'])
            if fresh and len(eligible)>=2 and eligible[0]['votes']>eligible[1]['votes']:
                color=PARTIAL_COLORS.get(eligible[0]['candidate_id'],'#746287')
                title=uf.upper()+' · liderança parcial: '+eligible[0]['name']+' · ainda pode mudar'
        if same:title+=' · mesmo líder do 1º turno'
        shapes.append(f'<a href="?uf={uf}" target="_self" aria-label="{html.escape(title)}"><path d="{path}" fill="{color}" fill-rule="evenodd"><title>{html.escape(title)}</title></path></a>')
        x,y=xy(LABELS[uf]); suffix=' •' if same else ''
        if uf in narrow:
            ly=140+narrow.index(uf)*25
            labels.append(f'<path d="M{x:.1f},{y:.1f} L430,{ly}" fill="none" style="stroke:#697260;stroke-width:.8"/>')
            x,y=442,ly+4
        labels.append(f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="middle">{uf.upper()}{suffix}</text>')
    description='Brasil: cores claras indicam liderança parcial; cores fortes indicam liderança matematicamente assegurada' if show_partial else 'Brasil: estados coloridos apenas com liderança matematicamente assegurada'
    return '<svg xmlns="http://www.w3.org/2000/svg" class="president-map" viewBox="0 0 470 460" role="img" aria-label="'+description+'"><style>path{stroke:#10110f;stroke-width:1.7}text{font:600 12px system-ui;fill:#c7cec2}</style>'+''.join(shapes+labels)+'</svg>'
