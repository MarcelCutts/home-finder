# EPC match experiment (parcel P7)

Can a rental listing be matched to its Energy Performance Certificate, so the
app can show each home's energy rating and floor area? Measured offline on
7 November 2026, against the register's API, with no portal requests.

## Answer

**No, not from listing addresses as they stand.** Matching on postcode, street
and house or flat number picks out a single home for **4 of 1,109 listings
(0.4%)**. 91% are ambiguous and 9% match nothing. Listings rarely give a
number: 6 give a flat number and 104 a house or building number, and 73% give
only a postcode district. Adding bedrooms (against the certificate's
habitable rooms) brings the total to **10 single matches (0.9%)**.

**The matches that are made look right, but few can be proven.** Only 10
single matches exist, so all 10 were hand-checked rather than a sample of 30.
The **correct-match rate is 9 of the 9 decided (95% interval 70–100%)**,
with one more that can't be told. But **only 2 of the 10 are proven** by a
fact naming the dwelling (a flat number, a lettered house number); the other
7 are consistent with the listing without proof. Two of those 7 are the same
flat, listed twice, so per home it is 8 of 8.

**EPC floor area is good enough to show as an approximate size, but not as an
exact one, and it can't be attached to listings at this coverage.** A flat
assessed twice gets a floor area a median 3.6% different; 75% of repeats agree
within 10%, and 10% disagree by more than 20%. The two matched homes with a
listing figure agree within 2%. So a correctly matched EPC area is a fair
guide to size, within 10% three times in four. The problem is the match, not
the number: at about 1% of listings, it isn't worth building on.

**The rating and floor area may be shown to renters under the Open Government
Licence, with attribution; the address match itself relies on restricted
address data** whose permitted purposes include energy efficiency in the
rental market. See [Licence](#can-renters-be-shown-the-rating-and-floor-area).

## Results

### Coverage

| | listings | full postcode | flat number | house or building number | floor area | advert band |
|---|---|---|---|---|---|---|
| October snapshot | 93 | 61 | 1 | 5 | 57 | 23 |
| February export | 1,016 | 238 | 5 | 99 | 143 | 24 |

"Floor area" is the app's floorplan reading or the portal's advertised figure.
"Advert band" is the EPC band the app's analysis read from the advert. A
listing with both a flat and a house number counts under flat number.

### Outcomes

| | listings | one home | ambiguous | no match |
|---|---|---|---|---|
| February | 1,016 | 3 | 918 (90%) | 95 (9%) |
| October | 93 | 1 | 86 (92%) | 6 (6%) |
| **all** | **1,109** | **4 (0.4%)** | **1,004 (91%)** | **101 (9%)** |
| with a full postcode | 299 | 4 (1%) | 250 (84%) | 45 (15%) |
| district only | 810 | 0 | 754 (93%) | 56 (7%) |

By the most specific fact used: street only, 772 (all ambiguous); building
name, 167 (all ambiguous); house or building number, 68 (3 single); flat
number, 1 (single).

Ambiguous listings have a median of **118 candidate homes** (quartiles 48 to
304): every certified home on the street at that postcode, or on the whole
street in that district. Only 6 have just two.

No match:

- 50 had no certificates to match against: 36 district-only listings whose
  street search found nothing in the district, 10 with no usable district or
  street, and 4 postcodes with no certificates;
- 35 gave a house number no certificate carries (24 of them are one block,
  below);
- 13 gave a street no candidate certificate carries (11 at their postcode, 2
  in their district);
- 3 gave only a building name no certificate carries.

### Narrowing by rooms

A certificate counts habitable rooms: bedrooms, living and dining rooms,
studies. All 4 strict matches have rooms equal to bedrooms plus one. 126
ambiguous listings have 25 or fewer candidates. For 8 of them no candidate's
certificate counts rooms, so the rule can't apply. For the other 118, the
candidates left are those with bedrooms plus one rooms, plus any whose rooms
aren't counted, since those can't be ruled out. That left **one home for 6
(5%)**, several for 106 and none for 6.

Uniqueness is among certified homes only. But a home offered for rent must
have an EPC, so the listing's home should be among the candidates if its
street and postcode are right.

### Hand-check

All 10 single matches, laid out against the listing's title, address variants,
bedrooms, the app's property type, floor and photo notes, and the advert text
where the database kept it (October only). Floor areas were left off the sheet
so the verdict couldn't lean on the agreement being measured.

| | correct (proven) | likely | wrong | unclear | correct-match rate |
|---|---|---|---|---|---|
| strict (postcode, street, number) | 2 | 1 | 0 | 1 | 3/3 |
| narrowed by rooms | 0 | 6 | 0 | 0 | 6/6 |
| **both** | **2** | **7** | **0** | **1** | **9/9 (70–100%)** |

*Correct*: a fact naming the dwelling ties them (a flat number; a lettered
house number), and type, rooms and age agree; one also matched the advert's
band. *Likely*: consistent on dwelling type, rooms, floor and build age, with
nothing specific. A building number alone counts as likely, since a converted
house holds several flats. *Unclear*: two dwellings share one address
(certificates for it lodged the same day disagree). Two of the narrowed
matches are the same flat, listed twice on OpenRent a week apart.

Before the narrowing fix two more narrowed matches existed. In both, some
candidates' certificates didn't count rooms and had been dropped; one was
judged wrong (the certificate's 1976–82 build age contradicted a period
conversion). Those verdicts are no longer counted.

### Floor area

| comparison | pairs | median difference | within 10% | within 20% |
|---|---|---|---|---|
| matched homes against the listing's figure | 2 | 1.6% | 2 | 2 |
| same flat, newest against previous certificate | 242 | 3.6% | 75% | 90% |
| same house, newest against previous certificate | 56 | 5.2% | 66% | 84% |
| median of the right-room candidates against the listing's figure | 21 | 9.2% | 52% | 76% |

Both listing figures in the first row are the app's floorplan readings.
The repeat-certificate rows are a seeded sample of 300 of the 66,263 homes
with two or more certificates in the searches made here. They test the EPC
figure without any listing match. Some of the difference is real (extensions,
conversions), and some pairs may be two dwellings sharing an address.
Some 2008–09 certificates (SAP schemas 12 and 13) don't state a total area;
for those the per-floor areas are summed. Across the 2,635 certificates
fetched here that state both, that sum is within 1% of the total for 93%.

The last row asks whether, when rooms leave several candidates, the median
floor area of those with the right rooms would do as an estimate. Half the
time it lands within 10%, which is too loose to show as a home's size.

### Rating

The 1 strict match with an advert band agrees with it. For the 42 ambiguous
listings whose advert states a band, the most common band among the
candidates is the advert's in 22 (52%) and within one band in 37 (88%).
Where every candidate shares one band (12% of ambiguous listings), all 4 that
can be checked agree with the advert. That is too few to rely on, and a
building-level band is not the home's own band.

## Failure patterns

- **No number in the address.** Portals give a street or a building name;
  street plus postcode leaves a median of 118 certified homes.
- **District only.** 73% of listings have no full postcode, so the
  candidates are the whole street within the district.
- **A building's UPRN on every flat.** The register often address-matches all
  flats of a converted house to the building's UPRN: 7,854 UPRNs in the
  searches here cover two or more addresses that each name a unit. Grouping on
  UPRN merged a ground floor and first floor flat into one "home" in the first
  hand-check, so certificates are grouped by normalised address instead.
- **Several dwellings at one address.** Certificates for the same address,
  lodged the same day, in different bands.
- **A house number that is really a flat number.** "Flat 12" on a street
  carries the same tokens as "12" on that street; the matcher now requires a
  house number not to follow "flat".

These came from inspecting the results rather than from a script:

- **Conversions keep the house's address.** A ground floor flat's address can
  carry the pre-conversion house's certificate, with the whole house's floor
  area and rooms.
- **A block with no certificates under its listed address.** One
  build-to-rent block, listed 24 times in February, has no certificate under
  its own number anywhere on its street, nor under its building name.
- **The portal's district is wrong.** 5 district-only listings found their
  street only in another district (this count is in the report).
- **Listing data errors.** One advertised floor area was 423.9 m² for a flat
  whose certificates say 44–53 m², likely square feet stored as square
  metres. The app's property type sometimes contradicts the certificate's
  build age.

## What would change the answer

- **The flat number.** Both matches made on a flat or lettered house number
  were proven. A renter who asks the agent for the full address, or enters it
  after booking a viewing, would get a reliable rating and size. That's a
  user-supplied lookup, not a background fill.
- **The advert's own EPC.** Rental adverts in commercial media must show the
  rating
  ([regulation 11](https://www.legislation.gov.uk/uksi/2012/3118/regulation/11)),
  and the app already detects EPC chart images in galleries. Reading the
  rating from the advert would likely reach more homes than matching does.
- **Not worth pursuing:** coordinates (listings with a full postcode are still
  84% ambiguous), building-level bands, and postcode-and-rooms floor-area
  estimates (both too loose, above).

## Can renters be shown the rating and floor area?

Read on 7 November 2026 from the register's
[licensing restrictions](https://get-energy-performance-data.communities.gov.uk/guidance/licensing-restrictions).
This is a reading of the terms, not legal advice.

**Rating and floor area: yes, with attribution.** Every field except the
address lines and postcode is under the Open Government Licence v3.0, UPRNs
included. The current band, SAP score, total floor area and certificate number
can be shown, commercially too, as long as the page says "Contains public
sector information licensed under the Open Government Licence v3.0" and
doesn't suggest the government endorses the product.

**The match itself uses restricted data.** Address lines and postcodes come
from Ordnance Survey's AddressBase and Royal Mail's PAF, and may be used only
for the purposes the notice lists. A listing can only be joined to a
certificate through those fields, so the join must fit one of them. The
closest is "promotion and better understanding of the current energy
efficiency of buildings … in the building sale or rental markets". Showing a
renter the rating of a home on the rental market fits that plainly. Floor
area is less clear-cut. It is an OGL field on the certificate, but a match
made mainly to get a home's size (for rent per m², say) is not an
energy-efficiency purpose. Showing it as part of the home's EPC summary keeps
it within the energy purpose.

**What must not happen:** republishing a certificate's address or postcode, or
keeping them beyond the match. Address data shared for a permitted purpose
must carry the Ordnance Survey and Royal Mail notice. This experiment keeps
them under the gitignored `data/`, as non-commercial research.

**Before building on it:** ask MHCLG (mhclg.digital-services@communities.gov.uk)
to confirm that matching listing addresses to certificates, to show renters
the rating and floor area, is within the energy-performance purpose. For a
commercial product, also ask whether an Ordnance Survey licence is needed.
The portal terms covering the listing addresses are a separate question
(see the product view).

## Method

**Listings.** Scratch copies of the October 2026 snapshot and the February
2026 export. A home on several portals keeps every portal's address line and
postcode. No listing has a UPRN.

**Certificates.** The register's domestic search over its API:

- full postcode: every certificate at that postcode;
- district only: a search on each street or building name, in the councils
  the district covers (from postcodes.io), kept to that district.

744 search result pages returned 297,717 certificates; 2,811 full
certificates were fetched for matches, narrowing and the retest.

**Matching** (`matcher.py`). Candidates are narrowed one listing fact at a
time: street, building, house or building number, flat. A street, number or
flat the listing states but no certificate carries means no match. With a
street, a building name narrows only when some certificate carries it; with
no street, a building name no certificate carries means no match. Certificates
are grouped into homes by normalised address and postcode; one home left is a
single match, several ambiguous. If two portals' addresses picked different
single homes, the listing would be ambiguous; none did.

**Narrowing** (`narrow.py`), **hand-check** (`handcheck.py`) and **retest**
(`retest.py`) as described above. `report.py` prints the coverage, outcome,
narrowing, hand-check, floor-area and rating numbers, and drops verdicts on
listings that are no longer single matches. `retest.py` prints the
repeat-certificate, per-floor-sum and shared-UPRN numbers. Figures listed
above as coming from inspection were counted by hand from the results.

## Reproduce

From this directory, with the token in `EPC_API_TOKEN` or
`~/.config/home-finder/epc_token`, and the two databases copied to
`data/inputs/{october,february}.db`:

```bash
uv run python run.py
```

```bash
uv run python narrow.py
```

```bash
uv run python handcheck.py && uv run python handcheck.py --narrowed
```

```bash
uv run python retest.py --homes 300
```

```bash
uv run python report.py
```

The hand-check verdicts are written by hand into
`data/handcheck/judgements*.csv`. Everything under `data/` (database copies,
API answers, per-listing results, hand-check sheets) holds addresses or
listing data and stays out of git. Answers are cached, so a rerun makes no
requests. Tests, from the repository root:

```bash
PYTHONUNBUFFERED=1 uv run pytest experiments/epc-match/tests
```
