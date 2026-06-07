"""
Metadata grind templates.

Phase 1 metadata you need to populate manually (Google Sheets recommended):

  1. stocks.csv               — Nifty 500 universe + basic facts
  2. commodities.csv          — tracked commodities + yf tickers
  3. stock_input_commodities.csv — links: which commodities feed which stocks

Export back as CSV, then `python -m scripts.load_metadata` imports them.

Suggested grind order (fastest to slowest):
  Week 1: stocks.csv (just symbol + sector + market_cap_cr) for top 100
  Week 2: stock_input_commodities.csv for top 100 (THE most valuable hour you spend)
  Week 3: extend stocks.csv to Nifty 500
  Week 4: input commodities for next 100 stocks
  Ongoing: promoter_group, global_parent fields as you research each
"""

CSV_TEMPLATES = {
    "stocks.csv": (
        "symbol_nse,symbol_yf,name,sector,sub_sector,market_cap_cr,"
        "in_nifty50,in_nifty100,in_nifty500,promoter_group,global_parent,notes\n"
        "RELIANCE,RELIANCE.NS,Reliance Industries,Oil & Gas,Refining,1900000,"
        "TRUE,TRUE,TRUE,Mukesh Ambani family,,\n"
        "TCS,TCS.NS,Tata Consultancy Services,IT,IT Services,1400000,"
        "TRUE,TRUE,TRUE,Tata Sons,,\n"
        "HITACHIENERGY,POWERINDIA.NS,Hitachi Energy India,Capital Goods,Electrical Equipment,30000,"
        "FALSE,TRUE,TRUE,Hitachi Group,Hitachi Energy Ltd (Switzerland),\n"
    ),
    "commodities.csv": (
        "code,name,unit,yf_ticker,category,active\n"
        "CRUDE_BRENT,Brent Crude Oil,USD/barrel,BZ=F,energy,TRUE\n"
        "CRUDE_WTI,WTI Crude Oil,USD/barrel,CL=F,energy,TRUE\n"
        "COPPER,Copper Futures,USD/lb,HG=F,metal,TRUE\n"
        "ALUMINIUM,Aluminium LME proxy,USD/ton,ALI=F,metal,TRUE\n"
        "STEEL_HRC,US HRC Steel,USD/ton,HRC=F,metal,TRUE\n"
        "GOLD,Gold Spot,USD/oz,GC=F,metal,TRUE\n"
        "NATURAL_GAS,Henry Hub Natural Gas,USD/MMBtu,NG=F,energy,TRUE\n"
        "PALM_OIL,Palm Oil,MYR/ton,,agri,TRUE\n"
    ),
    "stock_input_commodities.csv": (
        "symbol_nse,commodity_code,weight_pct,direction,notes\n"
        "ASIANPAINT,CRUDE_BRENT,40,negative,Major share of COGS via petrochemicals\n"
        "ASIANPAINT,NATURAL_GAS,10,negative,Production energy\n"
        "INDIGO,CRUDE_BRENT,35,negative,Jet fuel directly tracks crude\n"
        "MARUTI,STEEL_HRC,15,negative,Body panels\n"
        "MARUTI,ALUMINIUM,5,negative,Engine and structural\n"
        "TATASTEEL,COPPER,2,negative,Minor input\n"
        "RELIANCE,CRUDE_BRENT,55,negative,Feedstock for refining\n"
    ),
}


if __name__ == "__main__":
    import os
    here = os.path.dirname(__file__)
    for fname, content in CSV_TEMPLATES.items():
        path = os.path.join(here, fname)
        if os.path.exists(path):
            print(f"skip (exists): {path}")
            continue
        with open(path, "w") as f:
            f.write(content)
        print(f"wrote: {path}")
