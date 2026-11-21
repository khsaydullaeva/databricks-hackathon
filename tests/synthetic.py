"""Small synthetic datasets that mimic the real files' shape and quirks (for tests / local demo).
NOT real facilities."""

import json

import pandas as pd


def pincodes() -> pd.DataFrame:
    rows = [
        # circlename, regionname, divisionname, officename, pincode, officetype, delivery, district, statename, latitude, longitude
        ("Rajasthan", "Jaipur", "Jaipur City", "Jaipur G.P.O.", 302001, "HO", "Delivery", "JAIPUR", "RAJASTHAN", "26.9124", "75.7873"),
        ("Rajasthan", "Jaipur", "Jaipur City", "M.I. Road S.O", 302001, "PO", "Delivery", "JAIPUR", "RAJASTHAN", "26.9170", "75.8000"),
        ("Rajasthan", "Jaipur", "Jaipur City", "Bad Geocode B.O", 302001, "BO", "Delivery", "JAIPUR", "RAJASTHAN", "0", "0"),
        ("Rajasthan", "Jaipur", "Jaipur City", "Bapu Nagar S.O", 302004, "PO", "Delivery", "JAIPUR", "RAJASTHAN", "26.8900", "75.8100"),
        ("Rajasthan", "Jaipur", "Jaipur City", "Unmapped B.O", 302004, "BO", "Delivery", "JAIPUR", "RAJASTHAN", "NA", "NA"),
        ("Rajasthan", "Ajmer", "Ajmer", "Ajmer H.O", 305001, "HO", "Delivery", "AJMER", "RAJASTHAN", "26.4499", "74.6399"),
        ("Bihar", "Patna", "Patna", "Patna G.P.O.", 800001, "HO", "Delivery", "PATNA", "BIHAR", "25.6093", "85.1376"),
        ("Bihar", "Patna", "Patna", "Danapur S.O", 801503, "PO", "Delivery", "PATNA", "BIHAR", "25.6350", "85.0480"),
        ("Bihar", "Gaya", "Aurangabad", "Aurangabad H.O", 824101, "HO", "Delivery", "AURANGABAD", "BIHAR", "24.7521", "84.3742"),
        ("Bihar", "Gaya", "Aurangabad", "Border B.O", 824101, "BO", "Delivery", "GAYA", "BIHAR", "24.7600", "84.3900"),
        ("Maharashtra", "Aurangabad", "Aurangabad", "Aurangabad H.O", 431001, "HO", "Delivery", "AURANGABAD", "MAHARASHTRA", "19.8762", "75.3433"),
        ("Uttar Pradesh", "Lucknow", "Lucknow", "Lucknow G.P.O.", 226001, "HO", "Delivery", "LUCKNOW", "UTTAR PRADESH", "26.8467", "80.9462"),
        ("Delhi", "Delhi", "New Delhi", "Connaught Place S.O", 110001, "PO", "Delivery", "NEW DELHI", "DELHI", "28.6315", "77.2167"),
    ]
    cols = ["circlename", "regionname", "divisionname", "officename", "pincode", "officetype", "delivery",
            "district", "statename", "latitude", "longitude"]
    return pd.DataFrame(rows, columns=cols)


def nfhs() -> pd.DataFrame:
    return pd.DataFrame({
        "District Names": ["Jaipur", "Patna", "Aurangabad", "Aurangabad", "Lucknow"],
        "State/UT": ["Rajasthan", "Bihar", "Bihar", "Maharashtra", "Uttar Pradesh"],
        "Number of Households surveyed": ["1,234", "900", "880", "700", "1000"],
        "Households with any usual member covered under a health insurance or financing scheme (%)": ["40.1", "12.3", "*", "30.0", "20.5"],
        "Births delivered by caesarean section (%)": ["20.1", "(29.5)", "5.2", "25.0", "18.0"],
        "Women (age 15 years and above) - High or very high (>140 mg/dl) Blood sugar level (%)": ["6.1", "5.5", "4.9", "7.0", "6.3"],
    })


def facilities() -> pd.DataFrame:
    J = lambda xs: json.dumps(xs)
    base = dict(specialties=J([]), procedure=J([]), equipment=J([]), capability=J([]), description="",
                address_city="Jaipur", address_stateOrRegion="Rajasthan", address_zipOrPostcode="302001",
                latitude=26.912, longitude=75.787, numberDoctors=None, capacity=None, yearEstablished=None,
                source_url=J(["https://example.org/a"]), facilityTypeId="hospital", phone_numbers=J(["+91 141 000 0000"]),
                officialWebsite=None, address_line1=None)
    recs = [
        dict(pk_unique_id="F1", name="Jaipur Kidney Care Hospital", procedure=J(["Performs hemodialysis", "Kidney biopsy"]),
             equipment=J(["8 dialysis machines", "Ultrasound"]), capability=J(["24/7 emergency services", "Has 60 beds"]),
             specialties=J(["nephrology", "internalMedicine"]), capacity=60, numberDoctors=25, yearEstablished=2001,
             latitude=26.905, longitude=75.80, description="A kidney hospital. Hemodialysis available on all days."),
        dict(pk_unique_id="F1b", name="Jaipur Kidney Care Hospital", procedure=J(["Hemodialysis"]), latitude=26.906, longitude=75.801),
        dict(pk_unique_id="F2", name="City Nephro Clinic", specialties=J(["nephrology"]), facilityTypeId="clinic",
             latitude=26.92, longitude=75.79),
        dict(pk_unique_id="F3", name="Pink City Hospital", specialties=J(["generalSurgery"]),
             description="General hospital. We do not offer dialysis currently; patients are referred to the medical college.",
             latitude=26.89, longitude=75.81, address_zipOrPostcode="302004"),
        dict(pk_unique_id="F4", name="Sawai Multispeciality", capability=J(["Dialysis unit with 10 beds"]),
             address_zipOrPostcode="302004", latitude=28.63, longitude=77.21, source_url=J([])),
        dict(pk_unique_id="F5", name="Patna Mother Care", address_city="Patna", address_stateOrRegion="Bihar", address_zipOrPostcode="800001",
             latitude=25.61, longitude=85.14, procedure=J(["Caesarean section", "Normal delivery"]),
             capability=J(["Labour room", "24x7 emergency obstetric care"]), specialties=J(["gynecologyAndObstetrics"]), capacity=40),
        dict(pk_unique_id="F6", name="Tiny Child Clinic", address_city="Patna", address_stateOrRegion="Bihar", address_zipOrPostcode="801503",
             latitude=25.635, longitude=85.05, capability=J(["NICU available"]), facilityTypeId="clinic", capacity=5),
        dict(pk_unique_id="F7", name="Ajmer Dialysis Centre", address_city="Ajmer", address_zipOrPostcode="305001",
             latitude=None, longitude=None),
        dict(pk_unique_id="F8", name="Everything Hospital", address_city="Lucknow", address_stateOrRegion="Uttar Pradesh",
             address_zipOrPostcode="226001", latitude=26.85, longitude=80.95,
             procedure=J(["dialysis", "caesarean section", "cardiac surgery", "angioplasty", "MRI", "CT scan", "burn unit",
                          "trauma centre", "ICU", "chemotherapy", "radiotherapy", "blood bank", "neurosurgery",
                          "maternity", "kidney transplant", "cataract", "knee replacement", "psychiatry", "NICU"])),
        dict(pk_unique_id="F9", name="Shared Point A", address_city="Lucknow", address_stateOrRegion="Uttar Pradesh",
             address_zipOrPostcode="226001", latitude=26.8467, longitude=80.9462, capability=J(["ICU with ventilators"])),
        dict(pk_unique_id="F10", name="Shared Point B", address_city="Lucknow", address_stateOrRegion="Uttar Pradesh",
             address_zipOrPostcode="226001", latitude=26.8467, longitude=80.9462),
        dict(pk_unique_id="F11", name="Shared Point C", address_city="Lucknow", address_stateOrRegion="Uttar Pradesh",
             address_zipOrPostcode="226001", latitude=26.8467, longitude=80.9462),
        dict(pk_unique_id="F12", name="Shared Point D", address_city="Lucknow", address_stateOrRegion="Maharashtra",
             address_zipOrPostcode="226001", latitude=26.8467, longitude=80.9462),
        dict(pk_unique_id="F13", name="Aurangabad Bihar Hospital", address_city="Aurangabad", address_stateOrRegion="Bihar",
             address_zipOrPostcode="824101", latitude=24.75, longitude=84.37, capability=J(["Trauma care unit", "Casualty"])),
        dict(pk_unique_id="F14", name="Aurangabad MH Hospital", address_city="Aurangabad", address_stateOrRegion="Maharashtra",
             address_zipOrPostcode="431001", latitude=19.88, longitude=75.34, capability=J(["Trauma centre"])),
    ]
    return pd.DataFrame([{**base, **r} for r in recs])
