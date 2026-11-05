"""Conservative structural opportunity analysis; no claims of sale volume or fair value."""
from __future__ import annotations
from math import floor
import time

def assess(book: dict, cash: int, reserve: int, now: int|None=None) -> dict:
    """Analyse the cheapest price tier. No auction orders are ever placed.

    For commodity flips, the first tier must be cleared before reselling above it.
    The *next seller ask* is a scenario price, not proof the item will sell there.
    """
    now=int(time.time()) if now is None else now
    levels=sorted((int(p),int(q)) for p,q in book.get('levels',[]) if p>0 and q>0)
    available=max(0,cash-reserve)
    position_cap=floor(available*.25)
    result={'status':'SKIP','score':0,'reason':'No reliable order-book data',
            'buy_price':levels[0][0] if levels else None,
            'front_qty':levels[0][1] if levels else 0,'next_price':levels[1][0] if len(levels)>1 else None,
            'next_qty':levels[1][1] if len(levels)>1 else None,
            'entry_cost':levels[0][0]*levels[0][1] if levels else None,
            'target':None,'gross_theoretical':None,'roi':None,
            'age_seconds':max(0,now-int(book.get('observed_at',now))), 'depth':len(levels),
            'position_cap':position_cap}
    if not levels:return result
    if not book.get('full',False): result['status']='PARTIAL';result['reason']='Incomplete query results';return result
    if result['age_seconds']>600:result['status']='STALE';result['reason']='Saved depth is over 10 minutes old';return result
    buy,front=levels[0]
    vendor=int(book.get('vendor_copper',0) or 0)
    if vendor>buy:
        qty=min(front,available//buy)
        net=qty*(vendor-buy)
        result.update(target=vendor,gross_theoretical=net,roi=(vendor/buy-1)*100,
                      entry_cost=qty*buy,front_qty=front)
        if qty and net>=1:
            if book.get('kind')=='commodity':
                result.update(status='VENDOR',score=min(100,70+int(net/10)),
                              reason=f'NPC vendor pays {vendor}c per item; verify buyout and quantity live')
            else:
                result.update(status='VENDOR CHECK',score=min(100,55+int(net/10)),
                              reason=f'NPC pays at least {vendor}c for one item; verify entire auction cost / stacks')
        else:result.update(reason='Vendor spread too small for available cash')
        return result
    if book.get('kind')!='commodity':
        result['status']='REVIEW';result['reason']='Individual auctions may contain stacks; verify lot sizes in game';return result
    if len(levels)<2:result['reason']='No next price tier for a resale scenario';return result
    resale=levels[1][0]-1
    if resale<=buy:result['reason']='No positive price gap';return result
    if front>5:result['reason']=f'{front} units block resale: too many cheap units';return result
    cost=front*buy
    if cost>position_cap or cost>available:
        result['reason']=f'Clearing first tier costs {cost}c, above {position_cap}c position limit';return result
    per_unit_net=floor(resale*.95)-buy
    profit=front*per_unit_net
    roi=100*profit/cost if cost else 0
    result.update(entry_cost=cost,target=resale,gross_theoretical=profit,roi=roi)
    if profit<20 or roi<25:result['reason']='After-fee upside below 20c or 25% ROI';return result
    if levels[1][1]>30:
        result['status']='CAUTION';result['reason']='Next tier is crowded; demand is unknown'
    else:
        result['status']='INSPECT';result['reason']='Thin cheap tier; verify actual demand and listings'
    # Heuristic score, NOT a likelihood or expected value.
    result['score']=max(0,min(100,round(25+min(35,roi/3)+min(20,profit/30)
            -max(0,front-1)*5-max(0,(levels[1][1]-10)/3))))
    return result
