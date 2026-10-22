"""Print realism checks for the synthetic data, compared with typical Indian e-commerce numbers.

Run with:  python data/check_data.py
"""
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # so ₹ and emoji print on Windows terminals
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.db import connect  # noqa: E402
from engine.segments import SEGMENT_LABELS  # noqa: E402

conn = connect()
q = lambda sql, *a: conn.execute(sql, a).fetchall()  # noqa: E731

print("\n== Customer types (segments) ==")
for r in q("SELECT segment, COUNT(*) n, ROUND(AVG(total_spend)) spend, ROUND(AVG(churn_risk),2) churn "
           "FROM customer_features GROUP BY segment ORDER BY n DESC"):
    print(f"  {SEGMENT_LABELS[r['segment']]:<20} {r['n']:>5} customers | avg spend ₹{int(r['spend']):>6} | churn risk {r['churn']}")

rows = q("SELECT c.true_persona p, f.segment s FROM customers c JOIN customer_features f USING(customer_id)")
same = {"loyal": "loyal", "deal_hunter": "deal_hunter", "at_risk": "at_risk", "lapsed": "lapsed",
        "new": "new", "window_shopper": "window_shopper"}
match = sum(1 for r in rows if same.get(r["p"]) == r["s"] or (r["p"] == "regular" and r["s"] in ("regular", "cart_abandoner")))
print(f"  Segmentation matches hidden persona for {match / len(rows):.0%} of customers")

print("\n== Spending (should be long-tailed: a few big spenders) ==")
spend = sorted((r[0] for r in q("SELECT total_spend FROM customer_features WHERE orders_total > 0")), reverse=True)
total = sum(spend)
print(f"  Top 10% of buyers bring {sum(spend[:len(spend)//10]) / total:.0%} of revenue (typical: 40-50%)")
print(f"  Top 20% of buyers bring {sum(spend[:len(spend)//5]) / total:.0%} of revenue (typical: 60-70%)")
aov = sorted(r[0] for r in q("SELECT amount FROM orders"))
print(f"  Order value: median ₹{aov[len(aov)//2]}, mean ₹{sum(aov)//len(aov)}, 95th pct ₹{aov[int(len(aov)*.95)]}")

print("\n== Behaviour ==")
r = q("SELECT AVG(status='abandoned') FROM carts WHERE status != 'open'")[0][0]
print(f"  Cart abandonment rate: {r:.0%} (industry: ~70%)")
r = q("SELECT AVG(coupon_used), AVG(returned) FROM orders")[0]
print(f"  Orders using a coupon: {r[0]:.0%} | orders returned: {r[1]:.0%}")
for r in q("SELECT category, ROUND(AVG(returned)*100) ret FROM orders GROUP BY category ORDER BY ret DESC"):
    print(f"    returns in {r['category']:<12} {int(r['ret'])}%")
for r in q("SELECT c.city_tier t, ROUND(AVG(o.payment_method='COD')*100) cod FROM orders o JOIN customers c USING(customer_id) GROUP BY t"):
    print(f"  Cash on delivery in tier-{r['t']} cities: {int(r['cod'])}%")
r = q("SELECT COUNT(*) FROM customer_features WHERE open_cart_value > 0")[0][0]
print(f"  Customers with items in cart right now: {r}")
r = q("SELECT COUNT(*) FROM customer_features WHERE points_expiring_30d > 0")[0][0]
print(f"  Customers with reward points expiring in 30 days: {r}")

print("\n== Orders per month (expect spikes in festive/sale months) ==")
months = Counter(r[0] for r in q("SELECT substr(order_date,1,7) FROM orders"))
peak = max(months.values())
for m in sorted(months):
    print(f"  {m} {'█' * int(40 * months[m] / peak)} {months[m]}")
