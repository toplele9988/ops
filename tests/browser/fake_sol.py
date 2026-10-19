"""Stand-in for Sol in the browser harness: echoes only numbers already present in the DTO."""
import json, sys
req = json.load(sys.stdin)
dto = req['dto']
line = dto['lines'][0]
body = (f"Dear {dto['buyer']['contact'] or 'Sir'},\n\nThank you for your inquiry. Please find our quotation {dto['quote_no']} for "
        f"{line['qty']} units of {line['sku']} at USD {line['unit_price']:.2f} each, total USD {dto['totals']['total_amount']:,.2f} "
        f"({dto['incoterm']} {dto['named_place']}).\n\nBest regards")
print(json.dumps({'subject': f"Quotation {dto['quote_no']}", 'body': body}))
