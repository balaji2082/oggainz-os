"""
Tamil / English wording for the printed shift sheet.

Kept as two complete sets rather than one bilingual page: Balaji's call was to
print whichever the rider is comfortable reading, so `?lang=ta` renders a fully
Tamil sheet and `?lang=en` a fully English one. Same layout, same tick boxes,
same page count — only the words change.

Component names are translated too, because the ingredient line is the one thing
he reads under time pressure. Numbers and units stay as digits: '100g' reads the
same in both and mis-reading a quantity is the expensive mistake here.
"""

TEXT = {
    "en": {
        "dir": "ltr",
        "sheet_title":   "Shift Sheet",
        "kitchen":       "OG Gainz Kitchen",
        "slot":          "Slot",
        "date":          "Date",
        "orders":        "Orders",
        "staff":         "Staff",
        "Lunch":         "Lunch",
        "Dinner":        "Dinner",

        "timings":       "Shift timings",
        "t_prep":        "Prep starts",
        "t_pack":        "Packing done",
        "t_depart":      "Depart",
        "t_done":        "All delivered",

        "sec_prep":      "A · Batch prep — cook from this",
        "sec_orders":    "B · Orders — pack from this",
        "sec_route":     "C · Delivery route",
        "sec_closeout":  "D · Close-out",

        "prep_note":     "Total quantities for the whole slot. Tick each line when it is prepped.",
        "item":          "Item",
        "total":         "Total",
        "breakdown":     "Sizes",
        "prepped":       "Prepped",

        "order_note":    "One card per order. Check the note line before you close the box.",
        "customer":      "Customer",
        "phone":         "Phone",
        "address":       "Address",
        "contains":      "Contains",
        "note":          "NOTE",
        "packed":        "Packed",
        "checked":       "Checked",
        "delivered":     "Delivered",
        "no_items":      "Composition not set in the app — check with Balaji before packing",

        "route_note":    "Deliver in this order. Scan the code to open the full route in Google Maps.",
        "stop":          "Stop",
        "zone":          "Zone",
        "who":           "Delivered by",
        "eta":           "Approx.",
        "rider":         "You",
        "porter_handoff": "Hand to Porter at",
        "porter_direct": "Porter (from kitchen)",
        "scan":          "Scan for route",
        "total_km":      "Total distance",
        "back_by":       "Back at kitchen",
        "Hyperlocal":    "Hyperlocal",
        "Near":          "Near",
        "Mid":           "Mid",
        "Long":          "Long",

        "km_ridden":     "Km ridden today",
        "km_hint":       "Pre-filled from the route. Correct it if the real figure differed.",
        "allowance":     "Petrol allowance",
        "issues":        "Problems / anything to report",
        "signature":     "Rider signature",
        "depart_time":   "Left kitchen at",
        "return_time":   "Returned at",

        "warn_unpinned": "NO MAP PIN — ask Balaji for the location before you leave",
        "warn_missing":  "These orders have no map pin yet, so they are not in the route above. "
                         "They still have to be delivered.",
        "warn_nocomp":   "These orders have no saved composition, so they are NOT counted in the "
                         "prep totals above. Confirm with Balaji before cooking.",
        "warn_kitchen":  "Kitchen pin not set — distance and allowance are estimates only.",
        "warn_waypoints": "More than 9 stops: the Maps link only carries the first 9. "
                          "Follow the printed list for the rest.",
        "warn_late":     "This route does not close by %(end)s — about %(mins)s minutes over. "
                         "Move the farthest order to a Porter before he leaves.",
        "warn_estimate": "Distances below are ESTIMATES, not measured road distances. "
                         "Check the real figure in Google Maps before paying the allowance.",
        "measured_by":   "Road distance measured via %(src)s",
        "est_mark":      "approx.",
    },

    "ta": {
        "dir": "ltr",
        "sheet_title":   "ஷிஃப்ட் தாள்",
        "kitchen":       "OG Gainz சமையலறை",
        "slot":          "நேரம்",
        "date":          "தேதி",
        "orders":        "ஆர்டர்கள்",
        "staff":         "பணியாளர்",
        "Lunch":         "மதிய உணவு",
        "Dinner":        "இரவு உணவு",

        "timings":       "ஷிஃப்ட் நேரம்",
        "t_prep":        "தயாரிப்பு தொடங்கு",
        "t_pack":        "பேக்கிங் முடிவு",
        "t_depart":      "புறப்பாடு",
        "t_done":        "அனைத்தும் டெலிவரி",

        "sec_prep":      "A · மொத்த தயாரிப்பு — இதைப் பார்த்து சமையுங்கள்",
        "sec_orders":    "B · ஆர்டர்கள் — இதைப் பார்த்து பேக் செய்யுங்கள்",
        "sec_route":     "C · டெலிவரி வழி",
        "sec_closeout":  "D · நாள் முடிவு",

        "prep_note":     "இந்த நேரத்திற்கான மொத்த அளவு. ஒவ்வொன்றும் தயாரானதும் டிக் செய்யவும்.",
        "item":          "பொருள்",
        "total":         "மொத்தம்",
        "breakdown":     "அளவுகள்",
        "prepped":       "தயார்",

        "order_note":    "ஒரு ஆர்டருக்கு ஒரு கார்டு. பாக்ஸ் மூடும் முன் குறிப்பைப் படியுங்கள்.",
        "customer":      "வாடிக்கையாளர்",
        "phone":         "தொலைபேசி",
        "address":       "முகவரி",
        "contains":      "உள்ளடக்கம்",
        "note":          "குறிப்பு",
        "packed":        "பேக்",
        "checked":       "சரிபார்ப்பு",
        "delivered":     "டெலிவரி",
        "no_items":      "ஆப்-இல் விவரம் இல்லை — பேக் செய்யும் முன் பாலாஜியிடம் கேளுங்கள்",

        "route_note":    "இதே வரிசையில் டெலிவரி செய்யவும். முழு வழியைப் பார்க்க கோட்டை ஸ்கேன் செய்யவும்.",
        "stop":          "நிறுத்தம்",
        "zone":          "பகுதி",
        "who":           "யார் கொடுப்பது",
        "eta":           "தோராயமாக",
        "rider":         "நீங்கள்",
        "porter_handoff": "போர்ட்டரிடம் கொடுக்கவும்",
        "porter_direct": "போர்ட்டர் (சமையலறையிலிருந்து)",
        "scan":          "வழிக்கு ஸ்கேன்",
        "total_km":      "மொத்த தூரம்",
        "back_by":       "சமையலறைக்குத் திரும்ப",
        "Hyperlocal":    "மிக அருகில்",
        "Near":          "அருகில்",
        "Mid":           "நடுத்தரம்",
        "Long":          "தூரம்",

        "km_ridden":     "இன்று ஓட்டிய கி.மீ",
        "km_hint":       "வழியிலிருந்து முன்பே நிரப்பப்பட்டது. வித்தியாசம் இருந்தால் திருத்தவும்.",
        "allowance":     "பெட்ரோல் படி",
        "issues":        "பிரச்சனைகள் / சொல்ல வேண்டியவை",
        "signature":     "கையொப்பம்",
        "depart_time":   "புறப்பட்ட நேரம்",
        "return_time":   "திரும்பிய நேரம்",

        "warn_unpinned": "வரைபடத்தில் இடம் இல்லை — புறப்படும் முன் பாலாஜியிடம் கேளுங்கள்",
        "warn_missing":  "இந்த ஆர்டர்களுக்கு வரைபட இடம் இல்லை, அதனால் மேலே உள்ள வழியில் இல்லை. "
                         "இவற்றையும் டெலிவரி செய்ய வேண்டும்.",
        "warn_nocomp":   "இந்த ஆர்டர்களுக்கு விவரம் சேமிக்கப்படவில்லை, அதனால் மேலே உள்ள மொத்த "
                         "தயாரிப்பில் இவை சேர்க்கப்படவில்லை. சமைக்கும் முன் பாலாஜியிடம் உறுதி செய்யவும்.",
        "warn_kitchen":  "சமையலறை இடம் அமைக்கப்படவில்லை — தூரமும் படியும் தோராயமானவை மட்டுமே.",
        "warn_waypoints": "9-க்கு மேல் நிறுத்தங்கள்: வரைபட லிங்கில் முதல் 9 மட்டுமே. "
                          "மீதியை அச்சிடப்பட்ட பட்டியலில் பாருங்கள்.",
        "warn_late":     "இந்த வழி %(end)s மணிக்கு முடியாது — சுமார் %(mins)s நிமிடம் அதிகம். "
                         "புறப்படும் முன் தூரமான ஆர்டரை போர்ட்டருக்கு மாற்றவும்.",
        "warn_estimate": "கீழே உள்ள தூரங்கள் தோராயமானவை, உண்மையான சாலைத் தூரம் அல்ல. "
                         "படி வழங்கும் முன் Google Maps-இல் சரிபார்க்கவும்.",
        "measured_by":   "%(src)s மூலம் அளக்கப்பட்ட சாலைத் தூரம்",
        "est_mark":      "தோராயம்",
    },
}

# Menu components in Tamil. Anything not listed falls back to its English name.
COMPONENT_TA = {
    "Chicken":             "சிக்கன்",
    "Paneer":              "பன்னீர்",
    "Fish Fillet (Basa)":  "மீன் ஃபில்லே (பாசா)",
    "Egg":                 "முட்டை",
    "Carb of the day":     "இன்றைய கார்ப்",
    "Rice (of the day)":   "சாதம்",
    "Sweet Potato":        "சர்க்கரைவள்ளிக் கிழங்கு",
    "Chapathi (2 pc)":     "சப்பாத்தி (2)",
    "Oats":                "ஓட்ஸ்",
    "Veggies":             "காய்கறிகள்",
    "Fruits":              "பழங்கள்",
    "Yogurt (50ml)":       "தயிர் (50ml)",
    "Legumes / Sprouts":   "பயறு / முளைகட்டியவை",
    "Protein Shake":       "புரோட்டீன் ஷேக்",
    "Protein Salad":       "புரோட்டீன் சாலட்",
}


def t(lang):
    """The wording set for a language, defaulting to English."""
    return TEXT.get(lang, TEXT["en"])


def component_name(name, lang):
    if lang == "ta":
        return COMPONENT_TA.get(name, name)
    return name
