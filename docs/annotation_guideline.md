# Review Annotation Guideline (v1)

This guideline defines how to label apartment reviews for the Austin Housing Agent.
The same rules are used for human annotation, for the LLM labeling prompt, and for evaluating
the DeBERTa classifier, so **follow the rules even when you would personally judge a case differently**.
If a rule seems wrong, write it in the `notes` column and raise it at the checkpoint, instead of silently deviating.

## 1. What you label

Each review gets:

1. **One review type** (required): `lived_experience`, `prospective`, `staff_shoutout` or `generic`.
2. **13 topics**, each one of:
   - *empty*: the topic is not mentioned, or mentioned without any judgment;
   - `pos`: the review praises this aspect;
   - `neg`: the review complains about this aspect.

The sheet shows the star rating and the property name as context. Use them only to read the tone correctly
(e.g. sarcasm, or a short review whose meaning is unclear). **Every label must be supported by the text**:
a 1★ rating alone does not make any topic `neg`, and a 5★ "Great!" is still `generic` with no topics.

## 2. Workflow

1. Open `data/processed/gold/gold_v1_annotation.xlsx`, sheet `reviews`.
   Every label cell has a dropdown; do not type values by hand. Hover over a topic header to see a short definition.
   The `labels` sheet is a one-page summary of this guideline.
2. Read the whole review once, choose `review_type`, then go through the topic columns.
   Most reviews mention 0-3 topics; leave the rest empty.
3. **Checkpoint: stop after the first 50 rows (red line).** Run the validator (below), then review these
   50 with the project owner. Revise the guideline where needed, fix the 50 labels, and only then continue.
4. Use `notes` for anything uncertain: a rule that didn't fit, a label you hesitated over, a review that is not in English.
5. When finished, run the validator again and fix every reported problem:

   ```bash
   python -m src.labeling.load_gold --partial   # during annotation
   python -m src.labeling.load_gold             # final: all 500 rows must be labeled
   ```

Pace: roughly 30-60 seconds per review; 500 reviews take about 6-8 hours. Work in sessions of at most an hour.
Don't look up the property online or read its other reviews while labeling.

## 3. Review type (choose exactly one)

Decide in this order; the first rule that applies wins.

| Step | Question | Type |
|---|---|---|
| 1 | Does the author describe **anything concrete about living there**: the unit, the building, repairs, neighbors, noise, fees, safety, amenities they used, how management handled a problem? Former residents and renewals count. | `lived_experience` |
| 2 | Has the author only **toured, applied, signed, or is waiting to move in**, and describes that process or what they saw? | `prospective` |
| 3 | Is the review **only about staff members** (praise or complaint), with no concrete event or other aspect? | `staff_shoutout` |
| 4 | Otherwise: vague overall judgment with no concrete content. | `generic` |

Examples:

| Review | Type | Why |
|---|---|---|
| "Great place to live!" | `generic` | No concrete content |
| "Worst apartment ever. Avoid at all costs." | `generic` | Strong, but nothing concrete |
| "Jessica is the best! Thank you for everything!" | `staff_shoutout` | Only about a person |
| "Management is rude and useless." | `staff_shoutout` | Only about staff, no concrete event |
| "Thanks Sarah for the great tour!" | `staff_shoutout` | The tour is only the occasion for praising a person |
| "Toured today, units are spacious and it's close to campus. Sarah was super helpful." | `prospective` | Describes what they saw on the tour |
| "Just signed my lease, the process was quick and easy!" | `prospective` | Only the leasing process |
| "Carlos fixed my AC the same day I submitted the request." | `lived_experience` | A concrete repair event, even though it praises a person |
| "Lived here 2 years. Quiet, but the elevators break constantly." | `lived_experience` | Concrete living experience |
| "Just moved in, so far so good!" | `generic` | Has moved in but says nothing concrete |
| "They offered a gift card for 5-star reviews. Don't trust them." | `generic` | Only about the reviews themselves |

## 4. Topics

For each topic: what belongs there, what goes elsewhere, and typical `pos` / `neg` signals.

### `maintenance_repairs`
Repairs and in-unit systems: plumbing, leaks, hot water, AC/heating, appliances, washer/dryer, internet,
maintenance requests, maintenance staff doing repairs, unannounced entry for maintenance.
- **Judge by the outcome**: broken but fixed quickly = `pos`; slow, never fixed, repeated failures = `neg`.
- `pos`: "Maintenance is quick", "my dishwasher was fixed the next day".
- `neg`: "AC out for 2 weeks", "submitted 5 requests, nobody came", "constant leaks".
- Not here: elevators, doors, gates, key fobs (→ `elevators_access`); mold and pests (→ `pests_mold`).

### `elevators_access`
Elevators, key fobs, door locks, building entry, garage gates.
- `neg`: "elevators down for weeks", "fob stopped working", "garage gate broken again".
- Label elevator / door / gate problems **only** here, not also under `maintenance_repairs`.

### `pests_mold`
Roaches, rats, mice, bed bugs, ants, mold, mildew, persistent bad smells in the unit.
- `pos` is rare ("never had a bug problem"). `neg`: "roaches everywhere", "black mold in the vents".

### `noise`
Noisy neighbors, thin walls, construction, parties, fire alarms going off at night.
- `pos`: "very quiet". `neg`: "can hear everything through the walls", "fire alarm at 3am every week".

### `cleanliness`
Cleanliness of hallways, common areas, trash rooms and chutes, dog waste; condition of the unit at move-in (dirty, stained).
- `neg`: "hallways smell like trash", "unit was filthy when I moved in". `pos`: "common areas are always spotless".

### `safety_security`
Crime, car break-ins, theft, stolen packages, homeless people in the building, security guards and cameras, feeling safe.
- `pos`: "feel very safe here". `neg`: "car broken into twice", "packages stolen constantly".

### `parking`
Parking availability, garage, towing, guest parking, parking cost.
- `neg`: "towed from my own spot", "never enough guest parking".
- A parking **fee** complaint can be both `parking` and `fees_deposit`.

### `staff_management`
Staff and management attitude, communication, responsiveness, accountability, front desk, leasing office behavior, corporate.
- `pos`: "staff are friendly and helpful". `neg`: "never answer emails", "rude front desk", "management doesn't care".
- A `staff_shoutout` review **must** have `staff_management` set.
- Maintenance staff: label the repair under `maintenance_repairs`. Also label `staff_management` only if the review
  separately judges their attitude ("the maintenance guys are always friendly").

### `leasing_process`
Tours, applications, lease signing, renewals, move-in day logistics, unit not ready on move-in date, guarantor requirements.
- `pos`: "application process was smooth". `neg`: "move-in date delayed three times", "lease terms changed after signing".

### `fees_deposit`
Rent level and value for money, extra or hidden fees, utility bills, late fees, rent increases, security deposit, move-out charges.
- `pos`: "affordable for the area", "got my full deposit back". `neg`: "$200/month in hidden fees", "charged $900 for carpet cleaning".
- A neutral fact ("rent is $1,400") is not a judgment: leave empty.

### `amenities`
Shared facilities and services: pool, gym, study rooms, rooftop, events, coffee, dog park, package lockers as a facility.
- `pos`: "the rooftop pool is amazing". `neg`: "pool closed all summer", "gym equipment always broken".

### `unit_building`
The unit and building itself: size, layout, natural light, views, finishes, renovation, build quality, whether it matches the advertising.
- `pos`: "spacious units with great views". `neg`: "cheap construction, falling apart", "nothing like the photos".

### `location`
Location, neighborhood, walkability, distance to campus / downtown / stores.
- `pos`: "5 minutes walk to campus". `neg`: "nothing within walking distance".

## 5. General rules

1. **Judgment, not mention.** Label a topic only when the text praises or complains about it.
   "I asked about parking" or "rent is $1,400" → empty.
2. **Both praise and complaint on the same topic → `neg`.**
   "Maintenance is usually fast but they took three weeks to fix our AC" → `maintenance_repairs = neg`.
3. **Different topics are independent.** "Front desk is nice but there's mold" → `staff_management = pos`, `pests_mold = neg`.
4. **Type constraints** (the validator checks these):
   - `generic` → all topics empty.
   - `staff_shoutout` → only `staff_management` (pos or neg); all other topics empty.
   - `prospective` usually only involves leasing, staff, unit, amenities, location or fees. Living problems
     such as noise or pests mean the author lived there → `lived_experience`.
5. **Only this property.** Ignore comparisons with the author's previous apartment.
6. **The author's claims count.** "There are roaches" → `neg`, even if you can't verify it. Pure hearsay ("I heard there might be roaches") → empty.
7. **Updates in the same review.** If the author updates an earlier complaint ("UPDATE: they finally fixed it"), label the final state
   per topic, but if a problem lasted unreasonably long before being fixed, keep `neg` and note it.
8. **Sarcasm**: label the intended meaning ("Love waking up to the fire alarm at 3am" → `noise = neg`).
9. **Non-English or unreadable reviews**: set `generic`, leave topics empty, and write "non-English" in `notes`.

## 6. Worked examples

**A.** "I've lived here two years. Maintenance always comes the same day, the gym is great and it's a short walk to campus.
Only downside is parking is a nightmare on weekends."
- `lived_experience`; `maintenance_repairs = pos`, `amenities = pos`, `location = pos`, `parking = neg`

**B.** "Front desk staff are sweet but there is black mold in my bathroom and management ignores my emails."
- `lived_experience`; `staff_management = neg` (praise + complaint on the same topic → neg), `pests_mold = neg`

**C.** "Ximena was amazing during my tour! So helpful and knowledgeable."
- `staff_shoutout`; `staff_management = pos`

**D.** "Elevators broke three times this month and I live on the 18th floor. They charged us a $75 'amenity fee' too."
- `lived_experience`; `elevators_access = neg`, `fees_deposit = neg`

**E.** "Moved out in May and they kept my entire deposit for 'cleaning'. The unit was spotless."
- `lived_experience`; `fees_deposit = neg`

**F.** "Love it here!!!"
- `generic`; no topics

**G.** "Applied in March, they lost my application twice and the move-in date got pushed back a week."
- `prospective` (or `lived_experience` if they describe living there afterwards); `leasing_process = neg`

**H.** "Packages get stolen from the lobby all the time and there are people sleeping in the stairwells."
- `lived_experience`; `safety_security = neg`

## 7. Optional: self-consistency check

With a single annotator there is no second person to compare against. About a week after finishing,
relabel 50 randomly chosen rows in a copy of the sheet without looking at your earlier labels,
and report the agreement (Cohen's kappa per field). This shows how stable the labels are.

## Changelog

- v1 (2026-11-20): initial version; 4 review types, 13 topics with none / pos / neg.
- v1.1 (2026-11-22): the workbook shows the star rating and property name as context; labels still must be supported by the text.
