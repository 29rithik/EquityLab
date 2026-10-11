"""EquityLab single-file web app; Python 3.9+; no dependencies."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from dataclasses import dataclass, asdict, replace
from math import isfinite
import json, sys, os, time, re, urllib.request, urllib.parse, urllib.error, threading, io, datetime
@dataclass
class Inputs:
    revenue:float=416161
    revenue_growth:float=.065
    operating_margin:float=.30
    tax_rate:float=.21
    da_pct:float=.03
    capex_pct:float=.04
    nwc_pct:float=.02
    wacc:float=.09
    terminal_growth:float=.025
    cash:float=60000
    debt:float=100000
    other_claims:float=0
    diluted_shares:float=15000
    years:int=5

def parse(data):
    base=asdict(Inputs())
    if not isinstance(data,dict) or set(data)-set(base): raise ValueError('Unknown assumption fields')
    for k in base:
        try: base[k]=float(data.get(k,base[k]))
        except (TypeError,ValueError): raise ValueError(f'{k} must be numeric')
        if not isfinite(base[k]): raise ValueError(f'{k} must be finite')
    if not base['years'].is_integer() or not 1<=base['years']<=20: raise ValueError('Forecast years must be 1–20')
    base['years']=int(base['years']); a=Inputs(**base)
    if a.revenue<=0 or a.diluted_shares<=0 or a.revenue_growth<=-1: raise ValueError('Invalid revenue, growth, or shares')
    if not 0<a.wacc<=1 or a.wacc<=a.terminal_growth: raise ValueError('WACC must be positive and greater than terminal growth')
    for k in ['operating_margin','tax_rate','da_pct','capex_pct','nwc_pct']:
        if not 0<=getattr(a,k)<=1: raise ValueError(f'{k} must be between 0 and 100%')
    if min(a.cash,a.debt,a.other_claims)<0: raise ValueError('Cash, debt, claims cannot be negative')
    return a

def dcf(a):
    previous=a.revenue; rows=[]; pv=0
    for year in range(1,a.years+1):
        revenue=previous*(1+a.revenue_growth); ebit=revenue*a.operating_margin
        nopat=ebit*(1-a.tax_rate); da=revenue*a.da_pct; capex=revenue*a.capex_pct
        delta_nwc=(revenue-previous)*a.nwc_pct; fcff=nopat+da-capex-delta_nwc
        pv_fcff=fcff/(1+a.wacc)**year; pv+=pv_fcff
        rows.append(dict(year=year,revenue=revenue,ebit=ebit,nopat=nopat,da=da,capex=capex,delta_nwc=delta_nwc,fcff=fcff,pv_fcff=pv_fcff));previous=revenue
    tv=fcff*(1+a.terminal_growth)/(a.wacc-a.terminal_growth); pvtv=tv/(1+a.wacc)**a.years
    ev=pv+pvtv; equity=ev+a.cash-a.debt-a.other_claims
    return dict(rows=rows,pv_explicit=pv,pv_terminal=pvtv,terminal_value=tv,enterprise_value=ev,equity_value=equity,fair_value_per_share=equity/a.diluted_shares,terminal_share_pct=100*pvtv/ev if ev else None)

def compute(data):
    a=parse(data); matrix=[]
    for g in (-.005,0,.005):
        line={'growth':a.terminal_growth+g,'values':[]}
        for w in (-.01,-.005,0,.005,.01):
            aw=a.wacc+w; ag=a.terminal_growth+g
            price=dcf(replace(a,wacc=aw,terminal_growth=ag))['fair_value_per_share'] if aw>max(0,ag) else None
            line['values'].append({'wacc':aw,'price':price})
        matrix.append(line)
    return {'inputs':asdict(a),'valuation':dcf(a),'sensitivity':matrix,'notice':'Illustrative data; not live'}

# SEC integration: official EDGAR public JSON; no third-party packages required.
# Set SEC_USER_AGENT to an identifying application name and contact email.
SEC_AGENT=os.environ.get('SEC_USER_AGENT','Rithiks DCF Online ChubbiChoob@gmail.com').strip()
_CACHE={}; _LOCK=threading.Lock()
def sec_json(url, ttl=3600):
    if not SEC_AGENT or '@' not in SEC_AGENT:
        raise ValueError('Set SEC_USER_AGENT to an identifying name and real email before using SEC data')
    now=time.time()
    with _LOCK:
        cached=_CACHE.get(url)
        if cached and now-cached[0]<ttl:return cached[1]
    req=urllib.request.Request(url,headers={'User-Agent':SEC_AGENT,'Accept':'application/json','Accept-Encoding':'identity'})
    try:
        with urllib.request.urlopen(req,timeout=18) as response:
            if response.status!=200:raise ValueError(f'SEC returned HTTP {response.status}')
            raw=response.read(18_000_000)
            if len(raw)>=18_000_000:raise ValueError('SEC response too large')
            obj=json.loads(raw)
    except urllib.error.HTTPError as e:raise ValueError(f'SEC HTTP {e.code}; try again later')
    except (urllib.error.URLError,TimeoutError) as e:raise ValueError(f'SEC unavailable: {e}')
    with _LOCK:_CACHE[url]=(now,obj)
    return obj

def tickers():
    raw=sec_json('https://www.sec.gov/files/company_tickers.json',ttl=86400)
    return list(raw.values()) if isinstance(raw,dict) else raw

def company_match(q):
    q=q.strip().upper()
    if not q or len(q)>100:raise ValueError('Enter a ticker or company name')
    entries=tickers()
    exact=[x for x in entries if str(x.get('ticker','')).upper()==q]
    if exact:return exact[0]
    matches=[x for x in entries if q in x.get('title','').upper()]
    if not matches:raise ValueError('Company not found in SEC ticker mapping')
    if len(matches)>1:raise ValueError('Multiple matches: '+', '.join(x['ticker'] for x in matches[:8])+'. Enter a ticker.')
    return matches[0]

# XBRL tags may differ by company. Missing metrics are null, never zero.
TAGS={
 'revenue':['RevenueFromContractWithCustomerExcludingAssessedTax','SalesRevenueNet','Revenues','RevenueFromContractWithCustomerIncludingAssessedTax','SalesRevenueGoodsNet'],
 'operating_income':['OperatingIncomeLoss'],
 'net_income':['NetIncomeLoss'],
 'da':['DepreciationDepletionAndAmortization','DepreciationDepletionAndAmortizationPropertyPlantAndEquipment','DepreciationAmortizationAndAccretionNet'],
 'capex':['PaymentsToAcquirePropertyPlantAndEquipment','PaymentsForAdditionsToPropertyPlantAndEquipment','PaymentsToAcquireProductiveAssets'],
 'cash':['CashAndCashEquivalentsAtCarryingValue','CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'],
 'debt_current':['LongTermDebtCurrent','LongTermDebtAndCapitalLeaseObligationsCurrent','ShortTermBorrowings'],
 'debt_long':['LongTermDebtNoncurrent','LongTermDebtAndCapitalLeaseObligations'],
 'debt_total':['LongTermDebt','LongTermDebtAndCapitalLeaseObligations'],
 'diluted_shares':['WeightedAverageNumberOfDilutedSharesOutstanding'],
 'receivables':['AccountsNotesAndOtherReceivablesNetCurrent','AccountsReceivableNetCurrent'],
 'inventory':['InventoryNet'],
 'payables':['AccountsPayableCurrent'],
}
FLOW={'revenue','operating_income','net_income','da','capex','diluted_shares'}
def pick_annual(facts, names, unit, is_flow):
    for tag in names:
        entries=facts.get('us-gaap',{}).get(tag,{}).get('units',{}).get(unit,[])
        eligible=[]
        for e in entries:
            if e.get('form') not in ('10-K','10-K/A') or e.get('fp')!='FY':continue
            end=e.get('end'); start=e.get('start'); fy=e.get('fy')
            if not end or not isinstance(fy,int):continue
            if is_flow:
                if not start:continue
                from datetime import date
                try:duration=(date.fromisoformat(end)-date.fromisoformat(start)).days
                except ValueError:continue
                if not 330<=duration<=380:continue
            else:
                # Balance-sheet facts should represent the fiscal year-end, not an older comparative.
                if abs(int(end[:4])-fy)>1:continue
            eligible.append(e)
        if eligible:
            grouped={}
            for e in eligible:
                year=int(e['end'][:4]); key=(e['end'],e.get('filed',''))
                if year not in grouped or key>grouped[year][0]:grouped[year]=(key,e)
            return {year:{'value':item['val'],'end':item['end'],'filed':item.get('filed'),'accession':item.get('accn'),'tag':tag} for year,(_,item) in grouped.items()}
    return {}

def annual_data(company):
    cik=int(company['cik_str']); facts=sec_json(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json',ttl=3600)
    metrics={}
    for metric,names in TAGS.items():metrics[metric]=pick_annual(facts.get('facts',{}),names,'shares' if metric=='diluted_shares' else 'USD',metric in FLOW)
    years=sorted(set().union(*(set(v) for v in metrics.values())),reverse=True)[:10]
    rows=[]
    for y in years:
        vals={k:v.get(y) for k,v in metrics.items()}
        row={'year':y,'metrics':vals}
        rev=vals['revenue']['value'] if vals['revenue'] else None
        ebit=vals['operating_income']['value'] if vals['operating_income'] else None
        capex=vals['capex']['value'] if vals['capex'] else None
        da=vals['da']['value'] if vals['da'] else None
        row['operating_margin']=ebit/rev if rev and ebit is not None else None
        row['capex_ratio']=capex/rev if rev and capex is not None else None
        row['da_ratio']=da/rev if rev and da is not None else None
        rows.append(row)
    return {'ticker':company['ticker'],'name':company['title'],'cik':cik,'rows':rows,'source':f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json','retrieved_at_utc':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'units':'USD (raw SEC units), shares (raw SEC units)'}

def median(values):
    x=sorted(values)
    if not x:return None
    n=len(x);return x[n//2] if n%2 else (x[n//2-1]+x[n//2])/2

def recommendations(doc):
    rows=doc['rows']; usable=[r for r in rows if r['metrics']['revenue'] and r['metrics']['revenue']['value']>0]
    suggestions={}
    if len(usable)>=4:
        latest=usable[0]; past=usable[3]; span=latest['year']-past['year']
        if span>0:
            v=(latest['metrics']['revenue']['value']/past['metrics']['revenue']['value'])**(1/span)-1
            suggestions['revenue_growth']={'value':v,'method':'Revenue CAGR over '+str(span)+' fiscal-year intervals','years':[past['year'],latest['year']],'confidence':'medium'}
    for key,field in [('operating_margin','operating_margin'),('da_pct','da_ratio'),('capex_pct','capex_ratio')]:
        vals=[r[field] for r in rows[:5] if r[field] is not None]
        if len(vals)>=2:suggestions[key]={'value':median(vals),'method':'Median of '+str(len(vals))+' available annual observations','years':[r['year'] for r in rows[:5] if r[field] is not None],'confidence':'medium' if len(vals)>=3 else 'low'}
    suggestions['terminal_growth']={'value':.025,'method':'Illustrative analyst starting point; not company-derived','years':[],'confidence':'low'}
    suggestions['tax_rate']={'value':.21,'method':'Illustrative US federal statutory rate only; excludes state and foreign taxes','years':[],'confidence':'low'}
    # WACC requires market-based inputs. Do not invent it.
    suggestions['wacc']={'value':None,'method':'Not available: requires current risk-free rate, beta, equity risk premium, debt costs and capital structure','years':[],'confidence':'unavailable'}
    suggestions['nwc_pct']={'value':None,'method':'Not available: requires normalized operating current assets and liabilities','years':[],'confidence':'unavailable'}
    return suggestions

def model_prefill(doc):
    rows=doc['rows']; result={}; missing=[]
    if not rows:raise ValueError('No usable annual 10-K data found')
    latest=next((r for r in rows if r['metrics']['revenue']),None)
    if not latest:raise ValueError('No annual revenue data found')
    def get(metric):
        entry=latest['metrics'].get(metric)
        return entry['value'] if entry else None
    result['revenue']=get('revenue')/1e6
    for metric in ('cash','diluted_shares'):
        value=get(metric)
        if value is None:missing.append(metric)
        else:result[metric]=value/1e6
    current=get('debt_current');long=get('debt_long');total=get('debt_total')
    if current is not None and long is not None:result['debt']=(current+long)/1e6
    elif total is not None:result['debt']=total/1e6
    else:missing.append('debt')
    suggestions=recommendations(doc)
    for k,item in suggestions.items():
        if item['value'] is not None and k not in ('terminal_growth','tax_rate'):
            result[k]=item['value']
    # No fallback to fabricated WACC, NWC or missing balance sheet values.
    # Diluted weighted-average shares are a historical proxy, not a current fully diluted share count.
    return {'inputs':result,'missing':missing,'suggestions':suggestions,'baseline_fiscal_year':latest['year'],'note':'These are historical reference inputs, not a complete investment recommendation. Confirm fiscal dates and debt classification.'}


def excel_export(payload):
    try: import xlsxwriter
    except ImportError: raise ValueError('Install Excel dependency: pip install XlsxWriter')
    a=parse(payload.get('inputs',{}));v=dcf(a);doc=payload.get('company') or {}
    if not isinstance(doc,dict):raise ValueError('Invalid company')
    name=str(doc.get('name') or 'Illustrative model')[:100];ticker=str(doc.get('ticker') or 'DEMO')[:16]
    buf=io.BytesIO();wb=xlsxwriter.Workbook(buf,{'in_memory':True});wb.set_calc_mode('auto')
    title=wb.add_format({'bold':True,'font_size':19,'font_color':'white','bg_color':'#142640'})
    head=wb.add_format({'bold':True,'font_color':'white','bg_color':'#26527C'})
    inp=wb.add_format({'font_color':'#1655B8','num_format':'#,##0.00;[Red](#,##0.00)'})
    pct=wb.add_format({'font_color':'#1655B8','num_format':'0.00%'})
    calc=wb.add_format({'font_color':'#263449','num_format':'#,##0.00;[Red](#,##0.00)'})
    link=wb.add_format({'font_color':'#00834F','num_format':'#,##0.00;[Red](#,##0.00)'})
    pc=wb.add_format({'font_color':'#263449','num_format':'0.00%'})
    note=wb.add_format({'font_color':'#6A7890','italic':True})
    from xlsxwriter.utility import xl_col_to_name as col
    def sheet(n):
        w=wb.add_worksheet(n);w.hide_gridlines(2);w.set_column('A:A',37);w.set_column('B:V',17);w.freeze_panes(3,1);w.set_landscape();w.fit_to_pages(1,1);w.set_footer('Rithiks DCF Online | Research model | &P / &N');w.merge_range('A1:J1',n.upper(),title);w.set_row(0,32);return w
    summary,history,forecast,valuation,sensitivity,assumptions,sources=[sheet(n) for n in ['Investment Summary','Historical Financials','Forecast Model','DCF Valuation','Sensitivity Analysis','Assumptions','Sources & Checks']]
    fields=[('revenue','Base revenue ($M)'),('revenue_growth','Revenue growth'),('operating_margin','EBIT margin'),('tax_rate','Tax rate'),('da_pct','D&A / revenue'),('capex_pct','CapEx / revenue'),('nwc_pct','Incremental NWC / revenue'),('wacc','WACC'),('terminal_growth','Terminal growth'),('cash','Cash ($M)'),('debt','Debt ($M)'),('other_claims','Other claims ($M)'),('diluted_shares','Diluted shares (M)'),('years','Forecast years')]
    rate={'revenue_growth','operating_margin','tax_rate','da_pct','capex_pct','nwc_pct','wacc','terminal_growth'};ref={}
    assumptions.write_row('A3',['EDITABLE INPUT','VALUE'],head)
    for i,(key,label) in enumerate(fields,3):
        r=i+1;ref[key]=f"'Assumptions'!$B${r}";assumptions.write(i,0,label);assumptions.write_number(i,1,getattr(a,key),pct if key in rate else inp)
    assumptions.write('A20','Blue = hardcoded inputs; green = cross-sheet references.',note)
    assumptions.write('A21','All amounts in USD millions unless otherwise noted.',note)
    assumptions.write('A22','Change inputs in this sheet to recalculate Excel formulas.',note)
    assumptions.data_validation('B17',{'validate':'integer','criteria':'between','minimum':1,'maximum':20})
    forecast.write('B3','BASE YEAR',head)
    for i in range(20):forecast.write(2,i+2,f'Year {i+1}',head)
    labels=['Revenue','EBIT','NOPAT','D&A','CapEx','Change in NWC','FCFF','PV of FCFF']
    for i,label in enumerate(labels,4):forecast.write(i,0,label)
    forecast.write_formula('B5','='+ref['revenue'],link,a.revenue)
    for i in range(1,21):
        c=col(i+1);prev=col(i);r=v['rows'][i-1] if i<=a.years else None
        formulas={5:f'{prev}5*(1+{ref["revenue_growth"]})',6:f'{c}5*{ref["operating_margin"]}',7:f'{c}6*(1-{ref["tax_rate"]})',8:f'{c}5*{ref["da_pct"]}',9:f'{c}5*{ref["capex_pct"]}',10:f'({c}5-{prev}5)*{ref["nwc_pct"]}',11:f'{c}7+{c}8-{c}9-{c}10',12:f'{c}11/(1+{ref["wacc"]})^{i}'}
        keys={5:'revenue',6:'ebit',7:'nopat',8:'da',9:'capex',10:'delta_nwc',11:'fcff',12:'pv_fcff'}
        for row,expr in formulas.items():forecast.write_formula(f'{c}{row}',f'=IF({i}>{ref["years"]},"",{expr})',calc,r[keys[row]] if r else 0)
    metrics={4:('PV forecast FCFF',"=SUM('Forecast Model'!C12:V12)",v['pv_explicit']),5:('Final forecast FCFF',f"=INDEX('Forecast Model'!C11:V11,1,{ref['years']})",v['rows'][-1]['fcff']),6:('Terminal value',f'=IF({ref["wacc"]}<={ref["terminal_growth"]},NA(),B5*(1+{ref["terminal_growth"]})/({ref["wacc"]}-{ref["terminal_growth"]}))',v['terminal_value']),7:('PV terminal value',f'=B6/(1+{ref["wacc"]})^{ref["years"]}',v['pv_terminal']),8:('Enterprise value','=B4+B7',v['enterprise_value']),9:('Cash','='+ref['cash'],a.cash),10:('Debt','='+ref['debt'],a.debt),11:('Other claims','='+ref['other_claims'],a.other_claims),12:('Equity value','=B8+B9-B10-B11',v['equity_value']),13:('Diluted shares','='+ref['diluted_shares'],a.diluted_shares),14:('Intrinsic value / share','=IF(B13<=0,NA(),B12/B13)',v['fair_value_per_share']),15:('Terminal EV share','=IF(B8=0,NA(),B7/B8)',v['terminal_share_pct']/100 if v['terminal_share_pct'] is not None else 0)}
    for row,(label,formula,value) in metrics.items():valuation.write(row-1,0,label);valuation.write_formula(row-1,1,formula,pc if row==15 else calc,value)
    summary.write('A3','Company');summary.write('B3',name);summary.write('A4','Ticker');summary.write('B4',ticker)
    summary.write('A5','Data classification');summary.write('B5','Analyst / SEC snapshot' if doc.get('rows') else 'ILLUSTRATIVE')
    summary.write('A6','Exported UTC');summary.write('B6',datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M'))
    for row,txt,src in [(8,'Fair value / share',14),(9,'Enterprise value ($M)',8),(10,'Equity value ($M)',12),(11,'Terminal EV share',15)]:summary.write(row-1,0,txt);summary.write_formula(row-1,1,f"='DCF Valuation'!B{src}",pc if row==11 else link,metrics[src][2])
    summary.write('A14','No live market price supplied. Upside cannot be computed.',note)
    summary.write('A15','FCFF forecast is a simplified constant-ratio model.',note)
    sensitivity.write('A3','Terminal g / WACC',head)
    for j,offset in enumerate([-.01,-.005,0,.005,.01],1):sensitivity.write_formula(2,j,f'={ref["wacc"]}+({offset})',pc,a.wacc+offset)
    for i,offset in enumerate([-.005,0,.005],3):
        sensitivity.write_formula(i,0,f'={ref["terminal_growth"]}+({offset})',pc,a.terminal_growth+offset)
        for j,woff in enumerate([-.01,-.005,0,.005,.01],1):
            c=col(j);rr=i+1;w=a.wacc+woff;g=a.terminal_growth+offset
            terms='+'.join(f"IFERROR('Forecast Model'!{col(k+2)}11/(1+{c}$3)^{k+1},0)" for k in range(20))
            formula=f"=IF(OR({c}$3<=$A{rr},{c}$3<=0),NA(),(({terms})+('DCF Valuation'!$B$5*(1+$A{rr})/({c}$3-$A{rr}))/(1+{c}$3)^({ref['years']})+{ref['cash']}-{ref['debt']}-{ref['other_claims']})/{ref['diluted_shares']})"
            cache=dcf(replace(a,wacc=w,terminal_growth=g))['fair_value_per_share'] if w>max(0,g) else '#N/A'
            sensitivity.write_formula(i,j,formula,calc,cache)
    sensitivity.conditional_format('B4:F6',{'type':'3_color_scale','min_color':'#F7A9A5','mid_color':'#FFEAB6','max_color':'#9BDBC5'})
    history.write('A3','SEC reported historical values, USD millions',note)
    if doc.get('rows'):
        rows=sorted(doc['rows'],key=lambda x:x.get('year',0),reverse=True)[:10]
        for j,r in enumerate(rows,1):history.write(3,j,r.get('year'),head)
        for i,(key,lab) in enumerate([('revenue','Revenue'),('operating_income','Operating income'),('net_income','Net income'),('da','D&A'),('capex','CapEx'),('cash','Cash'),('debt_current','Current debt'),('debt_long','Long-term debt'),('diluted_shares','Diluted shares')],4):
            history.write(i,0,lab)
            for j,r in enumerate(rows,1):
                entry=r.get('metrics',{}).get(key)
                if isinstance(entry,dict) and isinstance(entry.get('value'),(int,float)):history.write_number(i,j,entry['value']/1e6,inp)
    else:history.write('A5','No SEC historical snapshot was supplied.',note)
    sources.write('A3','Source classification');sources.write('B3','SEC snapshot from browser' if doc.get('rows') else 'Illustrative / user input')
    sources.write('A4','Company');sources.write('B4',name)
    if doc.get('source','').startswith('https://data.sec.gov/'):sources.write_url('B5',doc['source'],string='Original SEC Company Facts')
    sources.write('A7','SEC retrieval UTC');sources.write('B7',str(doc.get('retrieved_at_utc','')))
    sources.write('A9','Model check: enterprise value')
    sources.write_formula('B9',"=IF(ABS('DCF Valuation'!B8-'DCF Valuation'!B4-'DCF Valuation'!B7)<0.01,\"PASS\",\"FAIL\")",None,'PASS')
    sources.write('A10','Model check: equity bridge')
    sources.write_formula('B10',"=IF(ABS('DCF Valuation'!B12-'DCF Valuation'!B8-'DCF Valuation'!B9+'DCF Valuation'!B10+'DCF Valuation'!B11)<0.01,\"PASS\",\"FAIL\")",None,'PASS')
    sources.write('A12','The workbook preserves user-provided inputs; SEC data is not re-verified on export.',note)
    sources.write('A13','Excel formulas recalculate when opened in Excel / compatible spreadsheet software.',note)
    chart=wb.add_chart({'type':'line'});chart.add_series({'name':'Revenue','categories':"='Forecast Model'!$C$3:$V$3",'values':"='Forecast Model'!$C$5:$V$5",'line':{'color':'#4479BA','width':2.5}});chart.set_title({'name':'Forecast Revenue'});chart.set_legend({'none':True});summary.insert_chart('D4',chart,{'x_scale':1.25,'y_scale':1.2})
    wb.close();return buf.getvalue(),ticker

HTML='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Rithiks DCF Online | Equity Research</title><style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700;800&family=Manrope:wght@400;500;600;700;800&display=swap');
:root{color-scheme:dark;--bg:#080e1b;--surface:#111c30;--surface2:#16243b;--line:#273954;--ink:#eef4ff;--muted:#a5b5cd;--accent:#7c9cff;--accent2:#93e3d3;--warning:#f5c57b;--danger:#ff9aaa}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(ellipse at 90% -10%,#1b2b50 0%,transparent 42%),var(--bg);color:var(--ink);font:14px 'DM Sans',system-ui,sans-serif;line-height:1.5}
header{min-height:74px;display:flex;align-items:center;gap:13px;padding:17px clamp(18px,4vw,50px);background:#0b1426ee;border-bottom:1px solid var(--line);font-family:Manrope,sans-serif;font-size:21px;font-weight:800;letter-spacing:-.65px}header em{font-style:normal;color:var(--accent)}.brandmark{display:grid;place-items:center;width:38px;height:38px;border-radius:12px;background:linear-gradient(135deg,#6889f9,#8a70e8);font-size:22px;color:white;box-shadow:0 6px 22px #6677e633}.headright{margin-left:auto;font:700 10px 'DM Sans',sans-serif;letter-spacing:1.8px;color:#91a4c0}
main{max-width:1510px;margin:auto;padding:30px clamp(16px,3vw,38px) 70px}h1,h2,h3{font-family:Manrope,sans-serif;letter-spacing:-.6px}h1{font-size:clamp(27px,3vw,38px);margin:0 0 6px;font-weight:800}h2{font-size:16px}h3{font-size:14px;font-weight:800;letter-spacing:.1px;margin:0 0 17px}.muted{color:var(--muted);font-size:12px}.warn{display:block;width:fit-content;padding:9px 13px;background:#3c3024;color:var(--warning);border:1px solid #71532f;border-radius:9px;margin:20px 0;font-size:11px;font-weight:700;letter-spacing:.45px}
.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin:21px 0}.card,.panel{background:linear-gradient(160deg,var(--surface),#0e192c);border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 10px 35px #02081720}.card{min-height:116px}.card strong{display:block;font:800 clamp(20px,2vw,29px) Manrope,sans-serif;letter-spacing:-.9px;margin-top:9px;overflow-wrap:anywhere}.card:first-child{border-color:#5d75b3;background:linear-gradient(145deg,#1c2c52,#111c30)}.card:first-child strong{color:#b6c8ff}.layout{display:grid;grid-template-columns:minmax(280px,335px) minmax(0,1fr);gap:17px}.stack{display:grid;gap:17px;align-content:start}.fields{display:grid;grid-template-columns:minmax(0,1fr) 112px;gap:10px;align-items:center}.fields label{font-size:12px;font-weight:600;color:#c8d4e6}.fields input,input#ticker{width:100%;background:#0a1427;color:var(--ink);border:1px solid #3a4d69;border-radius:9px;padding:10px 11px;font:600 13px 'DM Sans',sans-serif;outline:none}.fields input{text-align:right}.fields input:focus,input#ticker:focus{border-color:var(--accent);box-shadow:0 0 0 3px #7c9cff25}button{background:linear-gradient(135deg,#7595ff,#9a85ff);color:#080f23;border:0;padding:10px 14px;border-radius:9px;font:800 12px 'DM Sans',sans-serif;cursor:pointer;margin:5px 4px 0 0;transition:filter .15s,transform .15s}button:hover{filter:brightness(1.13);transform:translateY(-1px)}button:disabled{opacity:.4;cursor:not-allowed;transform:none}.alt{background:#253650;color:#e4edff;border:1px solid #364b6a}.scroll{overflow:auto;max-width:100%}table{border-collapse:collapse;width:100%;white-space:nowrap;font-size:12px}td,th{padding:11px 10px;border-bottom:1px solid #263751;text-align:right;font-variant-numeric:tabular-nums}th:first-child,td:first-child{text-align:left}th{color:#aebed6;font-size:11px;font-weight:700;background:#192740}tr:hover td{background:#18274055}.two{display:grid;grid-template-columns:1.15fr 1fr;gap:17px}.sens td,.sens th{text-align:center;border:1px solid #263751}.selected{outline:2px solid var(--accent);outline-offset:-2px;color:#b9c9ff;background:#30477588!important;font-weight:800}.bridge{display:grid;grid-template-columns:1fr auto;gap:11px;font-size:12px}.bridge span:nth-child(even){text-align:right;font-variant-numeric:tabular-nums}.total{color:#b6c8ff;border-top:1px solid #425677;padding-top:12px;font-weight:800}svg{width:100%;height:230px}code{color:#b7c7ff;font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.error{color:var(--danger);font-size:12px}a{color:#a9bdff!important}a:hover{text-decoration:underline}#activecompany{font-weight:700!important;font-size:15px!important;color:#a7c0ff!important}#companytable{margin:13px 0}#suggestions p{border-left:2px solid #536ca9;padding:5px 0 5px 10px;margin:7px 0}#datawarning{max-width:100%}
@media(max-width:1100px){.layout{grid-template-columns:300px minmax(0,1fr)}.two{grid-template-columns:1fr}}@media(max-width:820px){.layout{grid-template-columns:1fr}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.headright{display:none}}@media(max-width:480px){.cards{grid-template-columns:1fr}header{font-size:18px}.panel{padding:15px}}
</style></head><body><header><span class="brandmark">R</span><span>Rithiks <em>DCF Online</em></span><span class="headright">FINANCIAL RESEARCH WORKSPACE</span></header><main><h1>Valuation Dashboard</h1><h2 id="activecompany" style="color:#48dec2;margin:8px 0">DEMO MODEL — No company loaded</h2><div class="muted">Financial research &amp; valuation · USD millions · FCFF</div><div class="warn">ILLUSTRATIVE INPUTS — NOT LIVE DATA</div><div class="cards"><div class="card"><span class="muted">Fair value / share</span><strong id="fair">—</strong></div><div class="card"><span class="muted">Enterprise value ($M)</span><strong id="ev">—</strong></div><div class="card"><span class="muted">Equity value ($M)</span><strong id="equity">—</strong></div><div class="card"><span class="muted">Terminal EV contribution</span><strong id="share">—</strong></div></div><div class="layout"><aside class="stack"><section class="panel"><h3>Editable assumptions</h3><div class="fields" id="fields"></div><p><button id="calc">Recalculate</button><button class="alt" id="reset">Reset</button><button class="alt" id="bear">Bear</button><button class="alt" id="bull">Bull</button></p><div class="error" id="error"></div><p class="muted">Enter 9 for 9%. All currency and shares are in millions.</p></section><section class="panel"><h3>Export</h3><button class="alt" id="csv">CSV</button><button class="alt" id="json">JSON</button><button id="xlsx" type="button">Export Excel Model (.xlsx)</button><div class="muted" id="excelstatus" style="margin-top:9px" aria-live="polite">Exports all seven worksheets with editable Excel formulas.</div></section></aside><div class="stack"><section class="panel"><h3>Projected free cash flow</h3><svg id="chart" viewBox="0 0 700 230"></svg></section><section class="panel"><h3>Forecast schedule ($M)</h3><div class="scroll"><table><thead><tr><th>Year</th><th>Revenue</th><th>EBIT</th><th>NOPAT</th><th>D&A</th><th>CapEx</th><th>ΔNWC</th><th>FCFF</th><th>PV FCFF</th></tr></thead><tbody id="rows"></tbody></table></div></section><div class="two"><section class="panel"><h3>WACC × terminal growth</h3><div class="scroll"><table class="sens" id="sens"></table></div></section><section class="panel"><h3>Enterprise-to-equity bridge ($M)</h3><div class="bridge" id="bridge"></div></section></div><section class="panel"><h3>Calculation reference</h3><p class="muted"><code>Revenueₜ = Revenueₜ₋₁ × (1 + growth)</code><br><code>EBIT = Revenue × margin</code><br><code>NOPAT = EBIT × (1 − tax rate)</code><br><code>ΔNWC = ΔRevenue × NWC ratio</code><br><code>FCFF = NOPAT + D&A − CapEx − ΔNWC</code><br><code>PV FCFF = FCFF / (1 + WACC)ᵗ</code><br><code>Terminal value = FCFFₙ × (1 + g) / (WACC − g)</code><br><code>EV = Σ PV FCFF + PV terminal value</code><br><code>Equity = EV + cash − debt − other claims</code><br><code>Fair value = Equity / diluted shares</code></p><p class="muted">Simplified constant-ratio model. No live SEC data, linked three statements, or derived WACC.</p></section></div></div></main><script>
const defs=[['revenue','Revenue ($M)',416161],['revenue_growth','Revenue growth (%)',6.5],['operating_margin','Operating margin (%)',30],['tax_rate','Tax rate (%)',21],['da_pct','D&A / revenue (%)',3],['capex_pct','CapEx / revenue (%)',4],['nwc_pct','NWC / revenue (%)',2],['wacc','WACC (%)',9],['terminal_growth','Terminal growth (%)',2.5],['cash','Cash ($M)',60000],['debt','Debt ($M)',100000],['other_claims','Other claims ($M)',0],['diluted_shares','Diluted shares (M)',15000],['years','Forecast years',5]];const rates=new Set(['revenue_growth','operating_margin','tax_rate','da_pct','capex_pct','nwc_pct','wacc','terminal_growth']),E=id=>document.getElementById(id),F=n=>Number(n).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});let result=null;let activeCompany=null;E('fields').innerHTML=defs.map(([k,l,v])=>`<label for="${k}">${l}</label><input id="${k}" type="number" step="any" value="${v}">`).join('');function inputs(){return Object.fromEntries(defs.map(([k])=>[k,Number(E(k).value)/(rates.has(k)?100:1)]))}async function update(){try{E('error').textContent='';let r=await fetch('/api/dcf',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(inputs())});let d=await r.json();if(!r.ok)throw Error(d.error||'Calculation error');result=d;render(d)}catch(e){E('error').textContent=e.message}}
function render(d){let v=d.valuation,a=d.inputs;E('fair').textContent='$'+F(v.fair_value_per_share);E('ev').textContent='$'+F(v.enterprise_value);E('equity').textContent='$'+F(v.equity_value);E('share').textContent=F(v.terminal_share_pct)+'%';E('rows').innerHTML=v.rows.map(r=>'<tr>'+['year','revenue','ebit','nopat','da','capex','delta_nwc','fcff','pv_fcff'].map(k=>`<td>${k==='year'?r[k]:'$'+F(r[k])}</td>`).join('')+'</tr>').join('');let s=d.sensitivity;E('sens').innerHTML='<tr><th>g / WACC</th>'+s[0].values.map(x=>'<th>'+F(x.wacc*100)+'%</th>').join('')+'</tr>'+s.map((r,i)=>'<tr><th>'+F(r.growth*100)+'%</th>'+r.values.map((x,j)=>`<td class="${i===1&&j===2?'selected':''}">${x.price===null?'N/A':'$'+F(x.price)}</td>`).join('')+'</tr>').join('');let items=[['PV explicit FCFF',v.pv_explicit],['PV terminal',v.pv_terminal],['Enterprise value',v.enterprise_value],['+ Cash',a.cash],['− Debt',-a.debt],['− Other claims',-a.other_claims],['Equity value',v.equity_value]];E('bridge').innerHTML=items.map(([n,x],i)=>`<span class="${i===6?'total':''}">${n}</span><span class="${i===6?'total':''}">$${F(x)}</span>`).join('');let rows=v.rows,vals=rows.map(r=>r.fcff),lo=Math.min(0,...vals),hi=Math.max(0,...vals),range=hi-lo||1,X=i=>50+i*600/Math.max(1,rows.length-1),Y=n=>180-(n-lo)/range*145;let path=rows.map((r,i)=>`${i?'L':'M'}${X(i)},${Y(r.fcff)}`).join(' ');E('chart').innerHTML=`<line x1="40" y1="${Y(0)}" x2="670" y2="${Y(0)}" stroke="#64748b"/><path d="${path}" fill="none" stroke="#91a8ff" stroke-width="3"/>`+rows.map((r,i)=>`<circle cx="${X(i)}" cy="${Y(r.fcff)}" r="4" fill="#91a8ff"/><text x="${X(i)}" y="210" text-anchor="middle" fill="#c4d6e6" font-size="12">Y${r.year}</text>`).join('')}
let loaded=null;
function download(name,text,type){let u=URL.createObjectURL(new Blob([text],{type})),a=document.createElement('a');a.href=u;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}E('calc').onclick=update;E('reset').onclick=()=>{defs.forEach(([k,l,v])=>E(k).value=v);update()};E('bear').onclick=()=>{E('revenue_growth').value=2;E('operating_margin').value=25;update()};E('bull').onclick=()=>{E('revenue_growth').value=10;E('operating_margin').value=34;update()};E('csv').onclick=()=>{if(!result)return;let rows=result.valuation.rows,keys=Object.keys(rows[0]);download('rithiks_dcf_forecast.csv',[keys.join(','),...rows.map(r=>keys.map(k=>r[k]).join(','))].join('\\n'),'text/csv')};E('json').onclick=()=>{if(result)download('rithiks_dcf_model.json',JSON.stringify(result,null,2),'application/json')};E('xlsx').onclick=async()=>{const b=E('xlsx'),status=E('excelstatus');b.disabled=true;status.textContent='Building your Excel workbook…';try{let r=await fetch('/api/export/xlsx',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({inputs:inputs(),company:loaded})});if(!r.ok){let d=await r.json().catch(()=>({}));throw Error(d.error||'Excel export failed (HTTP '+r.status+')')}let blob=await r.blob();if(blob.size<1000)throw Error('The server returned an empty Excel file');let url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='Rithiks_DCF_'+(loaded?.ticker||'DEMO')+'.xlsx';document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);status.textContent='Excel workbook downloaded ('+(blob.size/1024).toFixed(0)+' KB).';}catch(e){status.textContent='Export error: '+e.message;E('error').textContent=e.message}finally{b.disabled=false}};defs.forEach(([k])=>E(k).addEventListener('change',update));update();

E('loadcompany').onclick=async()=>{
  const q=E('ticker').value.trim().toUpperCase();
  loaded=null;
  E('activecompany').textContent='Loading '+q+' — valuation not yet verified';
  E('activecompany').style.color='#fbbf24';
  E('datawarning').textContent='PENDING COMPANY LOAD — previous valuation is not valid for '+q+'.';
  E('applycompany').disabled=true;
  E('companytable').replaceChildren();
  E('suggestions').replaceChildren();
  E('companymsg').textContent='Loading SEC data for '+q+'...';
  if(!q){E('companymsg').textContent='Enter a ticker.';return;}
  try{
    const response=await fetch('/api/company?q='+encodeURIComponent(q),{cache:'no-store'});
    const data=await response.json();
    if(!response.ok)throw Error(data.error||'SEC request failed');
    loaded=data;
    E('activecompany').textContent=data.name+' ('+data.ticker+') — SEC data retrieved, not yet applied';
    E('activecompany').style.color='#48dec2';
    E('datawarning').textContent='SEC DATA RETRIEVED — apply historical inputs and review missing assumptions before using valuation.';
    E('companymsg').textContent=data.name+' ('+data.ticker+') · CIK '+data.cik+' · Retrieved '+data.retrieved_at_utc+' · '+data.prefill.note;
    const cols=['revenue','operating_income','net_income','da','capex','cash','debt_current','debt_long','diluted_shares'];
    const table=document.createElement('table');
    table.innerHTML='<thead><tr><th>Year</th>'+cols.map(k=>'<th>'+k+'</th>').join('')+'</tr></thead><tbody>'+data.rows.map(row=>'<tr><td>'+row.year+'</td>'+cols.map(k=>'<td>'+(row.metrics[k]?.value==null?'—':(row.metrics[k].value/1e6).toLocaleString('en-US',{maximumFractionDigits:2}))+'</td>').join('')+'</tr>').join('')+'</tbody>';
    E('companytable').append(table);
    const h=document.createElement('h3');h.textContent='Historical assumption suggestions';E('suggestions').append(h);
    for(const [key,v] of Object.entries(data.prefill.suggestions||{})){
      const line=document.createElement('p');line.className='muted';line.textContent=key+': '+(v.value==null?'Unavailable':(v.value*100).toFixed(2)+'%')+' — '+v.method+' ['+v.confidence+']';E('suggestions').append(line);
    }
    E('applycompany').disabled=false;
  }catch(err){E('activecompany').textContent='Company lookup failed — '+q+' not loaded';E('activecompany').style.color='#fb7185';E('companymsg').textContent='Error: '+err.message;}
};
E('applycompany').onclick=()=>{
  if(!loaded)return;
  for(const [k,v] of Object.entries(loaded.prefill.inputs||{})){
    if(E(k)&&Number.isFinite(v))E(k).value=rates.has(k)?(v*100).toFixed(5):v.toFixed(5);
  }
  E('activecompany').textContent=loaded.name+' ('+loaded.ticker+') — historical inputs applied (review required)';
  E('datawarning').textContent='PARTIAL SEC HISTORICAL DATA APPLIED — Verify shares, WACC, tax rate, working capital, and all other assumptions before relying on valuation.';
  update();
};
E('ticker').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();E('loadcompany').click();}});
</script></body></html>'''
HTML=HTML.replace('<div class="warn">ILLUSTRATIVE INPUTS — NOT LIVE DATA</div>','''<div class="panel" style="margin:14px 0"><h3>Company lookup · Official SEC EDGAR</h3><div style="display:flex;gap:8px;flex-wrap:wrap"><input id="ticker" placeholder="Ticker e.g. AAPL" value="AAPL" ><button id="loadcompany">Load SEC financials</button><button class="alt" id="applycompany" disabled>Apply SEC inputs</button></div><p class="muted" id="companymsg">SEC filing data requires SEC_USER_AGENT. Prices and WACC are not supplied by SEC.</p><div class="scroll" id="companytable"></div><div id="suggestions"></div></div><div class="warn" id="datawarning">ILLUSTRATIVE INPUTS — NOT LIVE DATA</div>''')
HTML=HTML.replace("E('xlsx').onclick=async()=>{try{let r=await fetch('/api/export/xlsx',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({inputs:inputs(),company:loaded})});if(!r.ok){let d=await r.json();throw Error(d.error||'Excel export failed')}let blob=await r.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='Rithiks_DCF_'+(loaded?.ticker||'DEMO')+'.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),3000)}catch(e){E('error').textContent=e.message}};defs.forEach(([k])=>E(k).addEventListener('change',update));update();",'let loaded=null;E(\'loadcompany\').onclick=async()=>{let q=E(\'ticker\').value.trim();E(\'companymsg\').textContent=\'Loading official SEC filings...\';E(\'companytable\').textContent=\'\';E(\'suggestions\').textContent=\'\';E(\'applycompany\').disabled=true;try{let r=await fetch(\'/api/company?q=\'+encodeURIComponent(q));let d=await r.json();if(!r.ok)throw Error(d.error||\'SEC request failed\');loaded=d;E(\'companymsg\').textContent=d.name+\' (\'+d.ticker+\') · CIK \'+d.cik+\' · Retrieved \'+d.retrieved_at_utc+\' · USD million display · \'+d.prefill.note;let names=[\'revenue\',\'operating_income\',\'net_income\',\'da\',\'capex\',\'cash\',\'debt_current\',\'debt_long\',\'diluted_shares\'];E(\'companytable\').innerHTML=\'<h3>Reported annual SEC data</h3><table><thead><tr><th>Fiscal year</th>\'+names.map(k=>\'<th>\'+k+\'</th>\').join(\'\')+\'</tr></thead><tbody>\'+d.rows.map(row=>\'<tr><td>\'+row.year+\'</td>\'+names.map(k=>\'<td>\'+((row.metrics[k]&&row.metrics[k].value!==null)?Number(row.metrics[k].value/1e6).toLocaleString(\'en-US\',{maximumFractionDigits:2}):\'—\')+\'</td>\').join(\'\')+\'</tr>\').join(\'\')+\'</tbody></table><p class="muted">Values in USD millions, except diluted shares in millions. Missing values are —. <a target="_blank" rel="noopener" href="\'+d.source+\'" style="color:#48dec2">Original SEC companyfacts ↗</a></p>\';E(\'suggestions\').innerHTML=\'<h3>Historical assumption suggestions (not analyst consensus)</h3>\'+Object.entries(d.prefill.suggestions).map(([k,v])=>\'<p class="muted"><b>\'+k+\'</b>: \'+(v.value===null?\'Unavailable\':(100*v.value).toFixed(2)+\'%\')+\' — \'+v.method+\' [\'+v.confidence+\']</p>\').join(\'\');E(\'applycompany\').disabled=false;}catch(e){E(\'companymsg\').textContent=\'Error: \'+e.message}};E(\'applycompany\').onclick=()=>{if(!loaded)return;for(let [k,v] of Object.entries(loaded.prefill.inputs)){if(E(k))E(k).value=rates.has(k)?(100*v).toFixed(5):v.toFixed(5)}E(\'datawarning\').textContent=\'PARTIAL SEC HISTORICAL DATA LOADED · WACC, TAX RATE, WORKING CAPITAL, SHARE COUNT AND OTHER ASSUMPTIONS REQUIRE ANALYST REVIEW · NO LIVE PRICE\';update()};defs.forEach(([k])=>E(k).addEventListener(\'change\',update));update();')
HTML=HTML.replace(".join('\\n'),'text/csv'", ".join(String.fromCharCode(10)),'text/csv'")
class Handler(BaseHTTPRequestHandler):
    def respond(self,status,data,mime):
        raw=data.encode('utf-8') if isinstance(data,str) else data
        self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        if self.path=='/': return self.respond(200,HTML,'text/html; charset=utf-8')
        if self.path.startswith('/api/company?'):
            try:
                q=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('q',[''])[0]
                doc=annual_data(company_match(q));doc['prefill']=model_prefill(doc)
                return self.respond(200,json.dumps(doc,allow_nan=False),'application/json')
            except (ValueError,OverflowError) as e:return self.respond(422,json.dumps({'error':str(e)}),'application/json')
            except Exception as e:return self.respond(502,json.dumps({'error':'Unable to retrieve SEC data: '+str(e)}),'application/json')
        if self.path=='/api/health': return self.respond(200,'{"status":"ok","version":"excel-stable-1"}','application/json')
        self.respond(404,'Not found','text/plain')
    def do_POST(self):
        if self.path=='/api/export/xlsx':
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<2000000:raise ValueError('Invalid export request size')
                payload=json.loads(self.rfile.read(length))
                content,ticker=excel_export(payload)
                self.send_response(200)
                self.send_header('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                self.send_header('Content-Disposition',f'attachment; filename="Rithiks_DCF_{ticker}.xlsx"')
                self.send_header('Content-Length',str(len(content)))
                self.end_headers();self.wfile.write(content)
            except (ValueError,TypeError,OverflowError) as e:self.respond(422,json.dumps({'error':str(e)}),'application/json')
            except Exception as e:self.respond(500,json.dumps({'error':'Excel export failed: '+str(e)}),'application/json')
            return
        if self.path!='/api/dcf': return self.respond(404,'Not found','text/plain')
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<100000: raise ValueError('Invalid request length')
            data=json.loads(self.rfile.read(length)); output=compute(data)
            self.respond(200,json.dumps(output,allow_nan=False),'application/json')
        except (ValueError,TypeError,OverflowError,ZeroDivisionError) as e:
            self.respond(422,json.dumps({'error':str(e)}),'application/json')
    def log_message(self,format,*args): pass

def tests():
    a=parse({});v=dcf(a)
    assert abs(v['enterprise_value']-v['pv_explicit']-v['pv_terminal'])<.0001
    assert abs(v['equity_value']-(v['enterprise_value']+a.cash-a.debt-a.other_claims))<.0001
    assert dcf(replace(a,wacc=.10))['fair_value_per_share']<v['fair_value_per_share']
    assert dcf(replace(a,revenue_growth=.08))['fair_value_per_share']>v['fair_value_per_share']
    assert len(compute({})['sensitivity'])==3
    for bad in ({'wacc':.01,'terminal_growth':.02},{'years':2.5},{'revenue':-10}):
        try: parse(bad);raise AssertionError('Expected rejection')
        except ValueError: pass
    print('PASS: 8 calculation and validation checks')
if __name__=='__main__':
    if '--test' in sys.argv: tests()
    else:
        import argparse
        parser=argparse.ArgumentParser(description='Rithiks DCF Online with SEC EDGAR lookup')
        parser.add_argument('port',nargs='?',type=int,default=8000)
        parser.add_argument('--user-agent',default=None,help='Identifying SEC user agent: application name and real contact email')
        args=parser.parse_args()
        if args.user_agent:
            SEC_AGENT=args.user_agent.strip()
        port=args.port
        if not SEC_AGENT or '@' not in SEC_AGENT:
            print('SEC lookup is not configured. Start with --user-agent "Rithiks DCF Online you@example.com" (use your real email), or set SEC_USER_AGENT.')
        print(f'Rithiks DCF Online running at http://localhost:{port}  (Ctrl+C to stop)')
        ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
