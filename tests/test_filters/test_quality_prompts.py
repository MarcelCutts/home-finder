"""Prompt snapshot regression tests for quality analysis prompts.

Uses inline-snapshot to catch unintended changes to prompt text and tool schemas.
First run: `uv run pytest tests/test_filters/test_quality_prompts.py --inline-snapshot=create`
On intentional prompt changes: `uv run pytest --inline-snapshot=update`
"""

from typing import Any

from inline_snapshot import snapshot

from home_finder.filters.quality import (
    EVALUATION_TOOL,
    VISUAL_ANALYSIS_TOOL,
)
from home_finder.filters.quality_prompts import (
    EVALUATION_SYSTEM_PROMPT,
    VISUAL_ANALYSIS_SYSTEM_PROMPT,
    build_evaluation_prompt,
    build_user_prompt,
)


class TestVisualSystemPromptSnapshot:
    """Snapshot tests for the Phase 1 visual analysis system prompt."""

    def test_prompt_starts_with_role(self) -> None:
        """System prompt should establish the analyst role."""
        assert VISUAL_ANALYSIS_SYSTEM_PROMPT.startswith(
            "You are an expert London rental property analyst"
        )

    def test_prompt_ends_with_tool_instruction(self) -> None:
        """System prompt should end with instruction to use the tool."""
        assert VISUAL_ANALYSIS_SYSTEM_PROMPT.endswith(
            "Always use the property_visual_analysis tool to return your assessment."
        )

    def test_prompt_contains_key_sections(self) -> None:
        """System prompt should contain all analysis sections."""
        key_sections = [
            "<task>",
            "<stock_types>",
            "<listing_signals>",
            "<analysis_steps>",
            "<rating_criteria>",
            "<output_rules>",
        ]
        for section in key_sections:
            assert section in VISUAL_ANALYSIS_SYSTEM_PROMPT, f"Missing section: {section}"

    def test_prompt_snapshot(self) -> None:
        """Full system prompt text should be stable."""
        assert VISUAL_ANALYSIS_SYSTEM_PROMPT == snapshot("""\
You are an expert London rental property analyst with perfect vision and meticulous attention to detail.

Your task is to observe and assess property quality from images and cross-reference with listing text. A separate evaluation step will handle value assessment, viewing preparation, and curation — focus purely on what you can see and verify.

When you cannot determine something from the images, use the appropriate sentinel value: "unknown" for enum/string fields, false for boolean fields, "none" for concern_severity when there are no concerns. For has_visible_damp, has_visible_mold, has_double_glazing, has_washing_machine, is_ensuite, primary_is_double, and can_fit_desk use "yes"/"no"/"unknown" — these are tri-state string fields. For multi-value fields (office_separation, hosting_layout, hosting_noise_risk) use "unknown" when you cannot determine the value. Do not guess — a confident "unknown" is more useful than a wrong answer.

<task>
Analyze property images (gallery photos and optional floorplan) together with listing text to produce a structured visual quality assessment. Cross-reference what you see in the images with the listing description — it often mentions "new kitchen", "gas hob", "recently refurbished" that confirm or clarify what's in the photos.
</task>

<stock_types>
First, identify the property type — this fundamentally affects expected pricing and what condition issues to look for:
- Victorian/Edwardian conversion: Period features, high ceilings, sash windows. Baseline East London stock. Watch for awkward subdivisions, original single glazing, rising damp, uneven floors.
- Purpose-built new-build / Build-to-Rent: Clean lines, uniform finish, large windows. Commands 15-30% premium but check for small rooms, thin partition walls, developer-grade finishes that wear quickly.
- Warehouse/industrial conversion: High ceilings, exposed brick, large windows. Premium pricing (especially E9 canalside). Watch for draughts, echo/noise, damp from inadequate conversion.
- Ex-council / post-war estate: Concrete construction, uniform exteriors, communal corridors. Should be 20-40% below area average. Communal area quality signals management standards.
- Georgian terrace: Grand proportions, original features. Premium stock.
</stock_types>

<listing_signals>
Scan the description for cost and quality signals to cross-reference with images:
- EPC rating: Band D-G = £50-150/month higher energy bills
- "Service charge" amount: Add to headline rent for true monthly cost
- "Rent-free weeks" or move-in incentives: Calculate effective monthly discount
- "Selective licensing" or licence number: Compliant landlord (positive)
- "Ground rent" or leasehold terms: Check for escalation clauses
- Proximity to active construction/regeneration: Short-term noise but potential rent increases (relevant in E9, E15, N17)
</listing_signals>

<analysis_steps>
1. Kitchen Quality: Modern (new units, integrated appliances, good worktops) vs Dated (old-fashioned units, worn surfaces, mismatched appliances). Note hob type if visible/mentioned. Check listing for "new kitchen", "recently fitted".

2. Property Condition: Look for damp (water stains, peeling paint near windows/ceilings), mold (dark patches in corners/bathrooms), worn fixtures (dated bathroom fittings, tired carpets, scuffed walls). Check stock-type-specific issues. Cross-reference listing mentions of "refurbished", "newly decorated".

3. Natural Light & Space: Window sizes, brightness, spacious vs cramped feel, ceiling heights if visible.

4. Living Room Size & Hosting Layout: From floorplan if included, estimate sqm. Target: fits a home office AND hosts 8+ people (~20-25 sqm minimum). Assess hosting_layout: how well does the layout flow for hosting 8+ guests? Consider kitchen-to-living connection (open-plan is ideal), bathroom accessibility without crossing bedrooms, practical entrance flow. Rate excellent/good/awkward/poor.

5. Overall Summary: 1-2 sentences — property character and what it's like to live here. Don't restate condition concerns (they're listed separately).

6. Overall Rating: 1-5 stars for rental desirability.

7. Bathroom: Condition, bathtub presence, shower type (overhead, separate cubicle, electric), ensuite. Cross-ref "wet room", "new bathroom", "recently refurbished bathroom" in description.

8. Bedroom & Office Separation: Can primary bedroom fit a double bed + wardrobe + desk? Check floorplan room labels and dimensions. "Double room" claims are often dubious. Assess office_separation quality: dedicated_room = closable second bedroom usable as office (non-through room with a door); separate_area = alcove, mezzanine, or partitioned nook; shared_space = desk in living room with no separation; none = studio or nowhere viable for a desk.

9. Storage: Built-in wardrobes, hallway cupboard, airing cupboard. London flats are notoriously storage-poor — flag when absent.

10. Outdoor Space: Balcony, garden, terrace, shared garden from photos or description. Premium London feature worth noting.

11. Flooring & Noise: Floor type (hardwood, laminate, carpet), double glazing presence, road-facing rooms, railway/traffic proximity indicators. Estimate building construction type from visual cues: solid brick (thick walls, period build), concrete (brutalist, ex-council, new-build blocks), timber frame (lightweight new-builds, stud wall indicators), mixed, or unknown. This affects sound insulation — solid brick and concrete are quieter. Assess hosting_noise_risk: risk of disturbing neighbours when hosting music/social events. low = solid construction + carpet + top floor or detached; moderate = mixed signals; high = timber frame + hard floors + lower floor + shared walls.

12. Floor Level: Estimate from photos and floorplan context — look for stairs in photos, "ground floor"/"first floor" in description, lift mentions, views from windows. Use: basement, ground, lower (1st-2nd), upper (3rd-4th), top (5th+), or unknown.

13. Red Flags: Missing room photos (no bathroom/kitchen photos), too few photos total (<4), selective angles hiding issues, description gaps or concerning language.
</analysis_steps>

<rating_criteria>
Overall rating (1-5 stars):
  5 = Exceptional: Modern/refurbished to high standard, excellent light/space, no concerns, good or excellent value. Rare find.
  4 = Good: Well-maintained, comfortable, minor issues at most. Fair or better value.
  3 = Acceptable: Liveable but with notable trade-offs (dated kitchen, limited light, average condition). Price should reflect this.
  2 = Below average: Multiple issues (poor condition, cramped, dated throughout). Only worth it if significantly below market.
  1 = Avoid: Serious problems (damp/mold, very poor condition, major red flags).
</rating_criteria>

<output_rules>
Each output field appears in a different section of the notification. Avoid restating information across fields:
- maintenance_concerns: Specific condition issues (shown in ⚠️ section)
- summary: Property character, layout, standout features (shown in blockquote)
If a fact belongs in one field, don't repeat it in another.
</output_rules>

Always use the property_visual_analysis tool to return your assessment.\
""")


class TestEvaluationSystemPromptSnapshot:
    """Snapshot tests for the Phase 2 evaluation system prompt."""

    def test_prompt_starts_with_role(self) -> None:
        """System prompt should establish the evaluator role."""
        assert EVALUATION_SYSTEM_PROMPT.startswith(
            "You are an expert London rental property evaluator"
        )

    def test_prompt_ends_with_tool_instruction(self) -> None:
        """System prompt should end with instruction to use the tool."""
        assert EVALUATION_SYSTEM_PROMPT.endswith(
            "Always use the property_evaluation tool to return your assessment."
        )

    def test_prompt_contains_key_sections(self) -> None:
        """System prompt should contain all evaluation sections."""
        key_sections = [
            "<task>",
            "<evaluation_steps>",
            "<value_rating_criteria>",
        ]
        for section in key_sections:
            assert section in EVALUATION_SYSTEM_PROMPT, f"Missing section: {section}"

    def test_prompt_snapshot(self) -> None:
        """Full evaluation system prompt text should be stable."""
        assert EVALUATION_SYSTEM_PROMPT == snapshot("""\
You are an expert London rental property evaluator. You have been given structured visual analysis observations from a detailed property inspection. Your job is to evaluate, synthesize, and prepare actionable information.

When you cannot determine something from the available data, use the appropriate sentinel value: "unknown" for enum/string fields. For bills_included and pets_allowed use "yes"/"no"/"unknown" — these are tri-state string fields. Only extract what is explicitly stated in the listing.

<task>
Given visual analysis observations and listing text, produce:
1. Structured data extraction from the listing description
2. Value-for-quality assessment grounded in the visual observations
3. Property-specific viewing preparation notes
4. Curated highlights and lowlights from the structured observations
5. A one-line property tagline
</task>

<evaluation_steps>
1. Listing Data Extraction: Mine the description for EPC rating, service charge, deposit weeks, bills included, pets allowed, parking, council tax band, property type, furnished status, broadband type. Only extract what is explicitly stated. For broadband_type: fttp = "fibre", "FTTP", "FTTH", "Hyperoptic", "Community Fibre", "full fibre", "1Gbps"; fttc = "superfast", "FTTC", "up to 80Mbps"; cable = "Virgin Media", "cable"; standard = "broadband" alone, ADSL. Use "unknown" if not mentioned.

2. Value Assessment: Consider stock type (new-build at +15-30% is expected, Victorian at +15% is overpriced, ex-council at average is poor value). Factor area context, true monthly cost (council tax, service charges, EPC costs, rent-free incentives), crime context, and rent trend trajectory. Ground your reasoning in the visual observations — reference specific findings like "modern kitchen" or "dated bathroom" to justify the rating. Your reasoning should focus on price-side factors — don't restate condition details.

3. Viewing Notes: Generate property-specific items to check during a viewing, questions for the letting agent, and quick deal-breaker tests. Base these on the visual analysis findings — if damp was flagged as unknown, suggest checking for it; if maintenance concerns were noted, suggest inspecting those areas. Be specific, not generic.

4. Highlights: Select 3-5 from the available highlight tags that best describe this property's positive features. Choose only tags supported by the visual evidence. Do NOT include EPC rating here.

5. Lowlights: Select 1-3 from the available lowlight tags that best describe this property's concerns or gaps.

6. One-liner: 6-12 word tagline capturing the property's character (e.g. "Bright Victorian flat with period features and a modern kitchen"). Synthesize from the visual observations.
</evaluation_steps>

<value_rating_criteria>
Value-for-quality rating:
  excellent = Quality clearly exceeds what this price normally buys in the area.
  good = Fair deal — quality matches or slightly exceeds the price point.
  fair = Typical for the price — no standout value, no major overpay.
  poor = Overpriced relative to quality/condition. Renter is overpaying.
</value_rating_criteria>

Always use the property_evaluation tool to return your assessment.\
""")


class TestBuildUserPromptSnapshot:
    """Snapshot tests for build_user_prompt output."""

    def test_minimal_prompt(self) -> None:
        """Minimal user prompt with just price/bedrooms/area_average."""
        prompt = build_user_prompt(
            price_pcm=1800,
            bedrooms=1,
            area_average=1900,
        )
        assert prompt == snapshot("""\
<property>
Price: £1,800/month | Bedrooms: 1 | Area avg: £1,900/month (£100 below)
</property>

Provide your visual quality assessment using the property_visual_analysis tool.\
""")

    def test_prompt_with_description(self) -> None:
        """User prompt with listing description included."""
        prompt = build_user_prompt(
            price_pcm=2000,
            bedrooms=2,
            area_average=2200,
            description="Spacious two bedroom flat with modern kitchen and garden.",
        )
        assert "<listing_description>" in prompt
        assert prompt == snapshot("""\
<property>
Price: £2,000/month | Bedrooms: 2 | Area avg: £2,200/month (£200 below)
</property>

<listing_description>
Spacious two bedroom flat with modern kitchen and garden.
</listing_description>

Provide your visual quality assessment using the property_visual_analysis tool.\
""")

    def test_prompt_with_area_context(self) -> None:
        """User prompt with area context and outcode."""
        prompt = build_user_prompt(
            price_pcm=1800,
            bedrooms=1,
            area_average=1900,
            area_context="Vibrant East London area with excellent transport links",
            outcode="E8",
        )
        assert '<area_context outcode="E8">' in prompt
        assert prompt == snapshot("""\
<property>
Price: £1,800/month | Bedrooms: 1 | Area avg: £1,900/month (£100 below)
</property>

<area_context outcode="E8">
Vibrant East London area with excellent transport links
</area_context>

Provide your visual quality assessment using the property_visual_analysis tool.\
""")

    def test_prompt_with_features(self) -> None:
        """User prompt with listing features."""
        prompt = build_user_prompt(
            price_pcm=1800,
            bedrooms=1,
            area_average=1900,
            features=["Double glazing", "Gas central heating", "Garden"],
        )
        assert "<listing_features>" in prompt
        assert prompt == snapshot("""\
<property>
Price: £1,800/month | Bedrooms: 1 | Area avg: £1,900/month (£100 below)
</property>

<listing_features>
- Double glazing
- Gas central heating
- Garden
</listing_features>

Provide your visual quality assessment using the property_visual_analysis tool.\
""")

    def test_prompt_with_all_context(self) -> None:
        """User prompt with all optional context fields."""
        prompt = build_user_prompt(
            price_pcm=1800,
            bedrooms=2,
            area_average=1900,
            description="A lovely 2-bed flat.",
            features=["Double glazing"],
            area_context="Trendy East London",
            outcode="E8",
            council_tax_band_c=150,
            crime_summary="75/1,000 (below vs London avg)",
            rent_trend="+5% YoY (rising)",
        )
        assert prompt == snapshot("""\
<property>
Price: £1,800/month | Bedrooms: 2 | Area avg: £1,900/month (£100 below)
Council tax (Band C est.): £150/month → True monthly cost: ~£1,950
</property>

<area_context outcode="E8">
Trendy East London
Crime: 75/1,000 (below vs London avg)
Rent trend: +5% YoY (rising)
</area_context>

<listing_description>
A lovely 2-bed flat.
</listing_description>

<listing_features>
- Double glazing
</listing_features>

Provide your visual quality assessment using the property_visual_analysis tool.\
""")


class TestBuildEvaluationPromptSnapshot:
    """Snapshot tests for build_evaluation_prompt output."""

    def test_minimal_evaluation_prompt(self) -> None:
        """Minimal evaluation prompt with just visual data and price context."""
        prompt = build_evaluation_prompt(
            visual_data={"kitchen": {"overall_quality": "modern"}},
            price_pcm=1800,
            bedrooms=1,
            area_average=1900,
        )
        assert "<visual_analysis>" in prompt
        assert "</visual_analysis>" in prompt
        assert prompt == snapshot("""\
<visual_analysis>
{
  "kitchen": {
    "overall_quality": "modern"
  }
}
</visual_analysis>

<property>
Price: £1,800/month | Bedrooms: 1 | Area avg: £1,900/month (£100 below)
</property>

Based on the visual analysis observations above, provide your evaluation using the property_evaluation tool.\
""")

    def test_evaluation_prompt_with_description(self) -> None:
        """Evaluation prompt with listing description."""
        prompt = build_evaluation_prompt(
            visual_data={"kitchen": {"overall_quality": "dated"}, "overall_rating": 3},
            description="Charming period flat in need of updating.",
            price_pcm=1600,
            bedrooms=1,
            area_average=1900,
        )
        assert "<listing_description>" in prompt
        assert prompt == snapshot("""\
<visual_analysis>
{
  "kitchen": {
    "overall_quality": "dated"
  },
  "overall_rating": 3
}
</visual_analysis>

<property>
Price: £1,600/month | Bedrooms: 1 | Area avg: £1,900/month (£300 below)
</property>

<listing_description>
Charming period flat in need of updating.
</listing_description>

Based on the visual analysis observations above, provide your evaluation using the property_evaluation tool.\
""")

    def test_evaluation_prompt_wraps_phase1_json(self) -> None:
        """Phase 1 JSON output should be faithfully wrapped in visual_analysis tags."""
        phase1_data: dict[str, Any] = {
            "kitchen": {"overall_quality": "modern", "hob_type": "gas"},
            "condition": {"overall_condition": "good"},
            "overall_rating": 4,
        }
        prompt = build_evaluation_prompt(
            visual_data=phase1_data,
            price_pcm=2000,
            bedrooms=2,
            area_average=2200,
        )
        # Verify JSON is faithfully embedded
        assert '"overall_quality": "modern"' in prompt
        assert '"hob_type": "gas"' in prompt
        assert '"overall_rating": 4' in prompt


class TestToolSchemaSnapshots:
    """Snapshot tests for tool schema structures."""

    def test_visual_analysis_tool_snapshot(self) -> None:
        """Full visual analysis tool schema should be stable."""
        assert VISUAL_ANALYSIS_TOOL == snapshot({'name':'property_visual_analysis','description':'Return visual property quality analysis results from images','input_schema':{'additionalProperties':False ,'properties':{'kitchen':{'additionalProperties':False ,'properties':{'overall_quality':{'description':'Overall kitchen quality/age assessment','enum':['modern','decent','dated','unknown'],'type':'string'},'hob_type':{'description':'Type of hob if visible or mentioned','enum':['gas','electric','induction','unknown'],'type':'string'},'has_dishwasher':{'enum':['yes','no','unknown'],'type':'string'},'has_washing_machine':{'enum':['yes','no','unknown'],'type':'string'},'notes':{'description':'Notable kitchen features or concerns','type':'string'}},'required':['overall_quality','hob_type','has_dishwasher','has_washing_machine','notes'],'type':'object'},'condition':{'additionalProperties':False ,'properties':{'overall_condition':{'enum':['excellent','good','fair','poor','unknown'],'type':'string'},'has_visible_damp':{'enum':['yes','no','unknown'],'type':'string'},'has_visible_mold':{'enum':['yes','no','unknown'],'type':'string'},'has_worn_fixtures':{'enum':['yes','no','unknown'],'type':'string'},'maintenance_concerns':{'description':'List of specific maintenance concerns','items':{'type':'string'},'type':'array'},'confidence':{'enum':['high','medium','low'],'type':'string'}},'required':['overall_condition','has_visible_damp','has_visible_mold','has_worn_fixtures','maintenance_concerns','confidence'],'type':'object'},'light_space':{'additionalProperties':False ,'properties':{'natural_light':{'enum':['excellent','good','fair','poor','unknown'],'type':'string'},'window_sizes':{'enum':['large','medium','small','unknown'],'type':'string'},'feels_spacious':{'description':'Whether the property feels spacious','type':'boolean'},'ceiling_height':{'enum':['high','standard','low','unknown'],'type':'string'},'floor_level':{'description':'Estimated floor level from photos/description/floorplan','enum':['basement','ground','lower','upper','top','unknown'],'type':'string'},'notes':{'type':'string'}},'required':['natural_light','window_sizes','feels_spacious','ceiling_height','floor_level','notes'],'type':'object'},'space':{'additionalProperties':False ,'properties':{'living_room_sqm':{'anyOf':[{'type':'number'},{'type':'null'}],'description':'Estimated living room size in sqm from floorplan'},'is_spacious_enough':{'description':'True if can fit office AND host 8+ people','type':'boolean'},'confidence':{'enum':['high','medium','low'],'type':'string'},'hosting_layout':{'description':'Layout flow for hosting: excellent = open-plan kitchen/living + accessible bathroom + practical entrance; good = mostly good flow; awkward = hosting friction (disconnected kitchen, narrow entrance, guests pass bedrooms); poor = fundamentally unsuitable (through-rooms, isolated kitchen)','enum':['excellent','good','awkward','poor','unknown'],'type':'string'}},'required':['living_room_sqm','is_spacious_enough','confidence','hosting_layout'],'type':'object'},'bathroom':{'additionalProperties':False ,'properties':{'overall_condition':{'enum':['modern','decent','dated','unknown'],'type':'string'},'has_bathtub':{'enum':['yes','no','unknown'],'type':'string'},'shower_type':{'enum':['overhead','separate_cubicle','electric','none','unknown'],'type':'string'},'is_ensuite':{'enum':['yes','no','unknown'],'type':'string'},'notes':{'type':'string'}},'required':['overall_condition','has_bathtub','shower_type','is_ensuite','notes'],'type':'object'},'bedroom':{'additionalProperties':False ,'properties':{'primary_is_double':{'enum':['yes','no','unknown'],'type':'string'},'has_built_in_wardrobe':{'enum':['yes','no','unknown'],'type':'string'},'can_fit_desk':{'enum':['yes','no','unknown'],'type':'string'},'office_separation':{'description':'Quality of work-life separation: dedicated_room = closable room for office (2-bed with non-through second room); separate_area = alcove, mezzanine, or partitioned nook; shared_space = desk in living room, no separation; none = studio or nowhere viable','enum':['dedicated_room','separate_area','shared_space','none','unknown'],'type':'string'},'notes':{'type':'string'}},'required':['primary_is_double','has_built_in_wardrobe','can_fit_desk','office_separation','notes'],'type':'object'},'outdoor_space':{'additionalProperties':False ,'properties':{'has_balcony':{'type':'boolean'},'has_garden':{'type':'boolean'},'has_terrace':{'type':'boolean'},'has_shared_garden':{'type':'boolean'},'notes':{'type':'string'}},'required':['has_balcony','has_garden','has_terrace','has_shared_garden','notes'],'type':'object'},'storage':{'additionalProperties':False ,'properties':{'has_built_in_wardrobes':{'enum':['yes','no','unknown'],'type':'string'},'has_hallway_cupboard':{'enum':['yes','no','unknown'],'type':'string'},'storage_rating':{'enum':['good','adequate','poor','unknown'],'type':'string'}},'required':['has_built_in_wardrobes','has_hallway_cupboard','storage_rating'],'type':'object'},'flooring_noise':{'additionalProperties':False ,'properties':{'primary_flooring':{'enum':['hardwood','laminate','carpet','tile','mixed','unknown'],'type':'string'},'has_double_glazing':{'enum':['yes','no','unknown'],'type':'string'},'building_construction':{'description':'Building construction type estimated from visual cues','enum':['solid_brick','concrete','timber_frame','mixed','unknown'],'type':'string'},'noise_indicators':{'items':{'type':'string'},'type':'array'},'hosting_noise_risk':{'description':'Risk of disturbing neighbours when hosting: low = solid construction + carpet + top floor/detached; moderate = mixed signals; high = timber frame + hard floors + lower floor + shared walls','enum':['low','moderate','high','unknown'],'type':'string'},'notes':{'type':'string'}},'required':['primary_flooring','has_double_glazing','building_construction','noise_indicators','hosting_noise_risk','notes'],'type':'object'},'listing_red_flags':{'additionalProperties':False ,'properties':{'missing_room_photos':{'description':"Rooms not shown in photos (e.g. 'bathroom', 'kitchen')",'items':{'type':'string'},'type':'array'},'too_few_photos':{'type':'boolean'},'selective_angles':{'type':'boolean'},'description_concerns':{'items':{'type':'string'},'type':'array'},'red_flag_count':{'description':'Total number of red flags identified','type':'integer'}},'required':['missing_room_photos','too_few_photos','selective_angles','description_concerns','red_flag_count'],'type':'object'},'overall_rating':{'description':'Overall 1-5 star rating for rental desirability (1=worst, 5=best)','type':'integer'},'condition_concerns':{'description':'True if any significant condition issues found','type':'boolean'},'concern_severity':{'enum':['minor','moderate','serious','none'],'type':'string'},'summary':{'description':"1-2 sentence property overview for notification. Focus on what it's like to live here: character, standout features, layout feel. Do NOT restate condition concerns (already listed separately).",'type':'string'}},'required':['kitchen','condition','light_space','space','bathroom','bedroom','outdoor_space','storage','flooring_noise','listing_red_flags','overall_rating','condition_concerns','concern_severity','summary'],'type':'object'}})

    def test_evaluation_tool_snapshot(self) -> None:
        """Full evaluation tool schema should be stable."""
        assert EVALUATION_TOOL == snapshot({'name':'property_evaluation','description':'Return property evaluation based on visual analysis observations','input_schema':{'additionalProperties':False ,'properties':{'listing_extraction':{'additionalProperties':False ,'properties':{'epc_rating':{'enum':['A','B','C','D','E','F','G','unknown'],'type':'string'},'service_charge_pcm':{'anyOf':[{'type':'integer'},{'type':'null'}]},'deposit_weeks':{'anyOf':[{'type':'integer'},{'type':'null'}]},'bills_included':{'enum':['yes','no','unknown'],'type':'string'},'pets_allowed':{'enum':['yes','no','unknown'],'type':'string'},'parking':{'enum':['dedicated','street','none','unknown'],'type':'string'},'council_tax_band':{'enum':['A','B','C','D','E','F','G','H','unknown'],'type':'string'},'property_type':{'enum':['victorian','edwardian','georgian','new_build','purpose_built','warehouse','ex_council','period_conversion','unknown'],'type':'string'},'furnished_status':{'enum':['furnished','unfurnished','part_furnished','unknown'],'type':'string'},'broadband_type':{'description':'Broadband type from listing: fttp = fibre/FTTP/FTTH/Hyperoptic/Community Fibre/full fibre/1Gbps; fttc = superfast/FTTC/up to 80Mbps; cable = Virgin Media/cable; standard = broadband alone/ADSL','enum':['fttp','fttc','cable','standard','unknown'],'type':'string'}},'required':['epc_rating','service_charge_pcm','deposit_weeks','bills_included','pets_allowed','parking','council_tax_band','property_type','furnished_status','broadband_type'],'type':'object'},'viewing_notes':{'additionalProperties':False ,'properties':{'check_items':{'description':'Property-specific things to inspect during viewing','items':{'type':'string'},'type':'array'},'questions_for_agent':{'description':'Questions to ask the letting agent','items':{'type':'string'},'type':'array'},'deal_breaker_tests':{'description':'Quick tests to determine deal-breakers','items':{'type':'string'},'type':'array'}},'required':['check_items','questions_for_agent','deal_breaker_tests'],'type':'object'},'highlights':{'description':'Top 3-5 positive features from the allowed highlight tags','items':{'enum':['Gas hob','Induction hob','Dishwasher included','Washing machine','Modern kitchen','Modern bathroom','Two bathrooms','Ensuite bathroom','Excellent natural light','Good natural light','Floor-to-ceiling windows','High ceilings','Spacious living room','Open-plan layout','Built-in wardrobes','Good storage','Private balcony','Private garden','Private terrace','Shared garden','Communal gardens','Roof terrace','Excellent condition','Recently refurbished','Period features','Double glazing','On-site gym','Concierge','Bike storage','Parking included','Pets allowed','Bills included','Canal views','Park views','Ultrafast broadband (FTTP)','Dedicated office room','Separate work area','Great hosting layout'],'type':'string'},'type':'array'},'lowlights':{'description':'Top 1-3 concerns from the allowed lowlight tags','items':{'enum':['No dishwasher','No washing machine','Dated kitchen','Electric hob','Compact living room','Small living room','Small bedroom','Compact bedroom','Poor storage','No storage','Dated bathroom','No outdoor space','No interior photos','No bathroom photos','Missing key photos','Potential traffic noise','New-build acoustics','Service charge unstated','Balcony cracking','Needs updating','Basic broadband only','No work-life separation','Poor hosting layout'],'type':'string'},'type':'array'},'one_line':{'description':"6-12 word tagline capturing the property's character",'type':'string'},'value_for_quality':{'additionalProperties':False ,'properties':{'rating':{'description':'Value rating considering quality vs price','enum':['excellent','good','fair','poor'],'type':'string'},'reasoning':{'description':"Value justification: why this price is or isn't fair for what you get. Focus on price factors: stock type premium/discount, true monthly cost, area rent trajectory, service charges, incentives. Reference condition only as 'condition justifies/doesn't justify price' — don't restate specific issues.",'type':'string'}},'required':['rating','reasoning'],'type':'object'}},'required':['listing_extraction','viewing_notes','highlights','lowlights','one_line','value_for_quality'],'type':'object'},'strict':True })

    def test_visual_tool_name(self) -> None:
        """Visual tool should have the correct name."""
        assert VISUAL_ANALYSIS_TOOL["name"] == "property_visual_analysis"

    def test_evaluation_tool_name(self) -> None:
        """Evaluation tool should have the correct name."""
        assert EVALUATION_TOOL["name"] == "property_evaluation"

    def test_evaluation_tool_is_strict(self) -> None:
        """Evaluation tool should have strict mode enabled."""
        assert EVALUATION_TOOL.get("strict") is True

    def test_visual_tool_is_not_strict(self) -> None:
        """Visual tool should not have strict mode (schema too complex)."""
        assert "strict" not in VISUAL_ANALYSIS_TOOL
