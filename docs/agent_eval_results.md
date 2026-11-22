# Agent evaluation (MLflow GenAI evaluate)

- Agent model: `databricks-meta-llama-3-3-70b-instruct` · judge model: `databricks-llama-4-maverick` · 20 cases · MLflow run `f4e0806fe666438e82e0310941121c89` in experiment `/Users/ksaydull@andrew.cmu.edu/findcare-agent`

| Check | Pass rate |
|---|---|
| asks_when_ambiguous | 100% |
| cites_evidence | 94% |
| correct_location | 100% |
| correct_service | 100% |
| declines_out_of_scope | 100% |
| grounded_in_tools | 90% |
| judge_guidelines | 90% |
| respects_public_only | 100% |
| tells_to_call_ahead | 100% |
| user_saw_verified_answer | 100% |

## Per case

| Request | correct_service | correct_location | grounded_in_tools | user_saw_verified_answer | cites_evidence | asks_when_ambiguous | declines_out_of_scope | respects_public_only | tells_to_call_ahead | judge |
|---|---|---|---|---|---|---|---|---|---|---|
| My patient's kidneys have failed. She lives near Gaya in Bihar and can | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ✅ |
| Need dialysis for a diabetic man near Jaipur | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ✅ |
| dialysis kahan milega Varanasi mein? | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ✅ |
| Pregnant woman in obstructed labour, we are at PIN 226001, need an eme | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ✅ |
| High-risk pregnancy, twins, 36 weeks, near Lucknow – which hospital ca | ✅ | ✅ | ❌ | ✅ | ✅ | – | – | – | ✅ | ✅ |
| Newborn born at 30 weeks, struggling to breathe, we are in Darbhanga | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ✅ |
| Premature baby needs incubator care near Patna | ✅ | ✅ | ✅ | ✅ | ❌ | – | – | – | ✅ | ✅ |
| Road accident on the highway near Agra – where is the nearest trauma c | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ✅ |
| Man with crushing chest pain, suspected heart attack, in Pune | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ✅ |
| Elderly patient needs open-heart bypass surgery, family is in Jaipur | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ✅ |
| Where can a patient get an MRI scan in Guwahati? | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ✅ |
| Cancer patient needs chemotherapy near Indore | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ✅ |
| Patient on a ventilator needs an ICU bed, location 25.61, 85.14 | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ❌ |
| Government hospital for a normal delivery near Patna, the family canno | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | ✅ | ✅ | ✅ |
| Dialysis near Aurangabad | ✅ | ✅ | ✅ | ✅ | – | ✅ | – | – | – | ✅ |
| Where can my patient get dialysis? | ✅ | – | ✅ | ✅ | – | ✅ | – | – | – | ✅ |
| What medicine should I give a child with high fever? | – | – | ✅ | ✅ | – | – | ✅ | – | – | ❌ |
| Severe head injury after a fall, need neurosurgery near Kochi | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ✅ |
| Cataract surgery for my grandmother near Madurai | ✅ | ✅ | ✅ | ✅ | ✅ | – | – | – | ✅ | ✅ |
