{#- The weekly draft as Markdown, for Andy to edit, add his take to and paste into Substack.
    Uses only what the Desktop "Copy for Substack" converter handles: ## headings, - bullets,
    **bold**, *italic*, [links](url) and plain paragraphs. Lines in [square brackets] are notes
    for Andy and get deleted before sending.
    It's about how the press covered the week. Every count is across the outlets we track, and it
    never says who broke a story. -#}
{% macro tks(syms) %}{% if syms %} {{ syms[:3]|join(", ") }}{% endif %}{% endmacro -%}
{% macro story(g) %}**[{{ g.headline | md }}]({{ g.link }})** ({{ g.outlet }}{% if g.paywall %}, £{% endif %}){{ tks(g.symbols) }}{% endmacro -%}
TITLE IDEAS (pick one, then delete these lines)
{% for t in titles %}{{ loop.index }}. {{ t }}
{% else %}[No title ideas this week. Write your own.]
{% endfor %}
---

All Tech Stocks Weekly, {{ week_label }}

{% if pieces.intro %}[Suggested intro below. Edit it, then delete this line. Report, don't tip.]
{{ pieces.intro | md }}{% else %}[Your intro: four or five sentences on where the press attention went this week. Report, don't tip.]{% endif %}

## Andy's take

[This is the part readers pay for. Two or three paragraphs in your own words on what the week's coverage says about the companies. Nothing on the shares, no predictions.]
{% if pieces.angles %}[Angles you could take. Delete these lines when you're done:]
{% for a in pieces.angles %}[{{ loop.index }}. {{ a | md }}]
{% endfor %}{% endif %}
Andy
{% if facts.big3 %}
## The Big 3

The three stories the most outlets wrote up this week, and how each one played out.

{% for g in facts.big3 %}{{ story(g) }}
- {{ g.outlets }} outlets and {{ g.articles }} articles
{% if g.to_big is not none %}- Reached {{ facts.pace.big }} outlets {{ g.to_big | hours }} after the first article we saw
{% endif %}- Most of the coverage (90% of articles) was out within {{ g.most_within | hours }}, busiest on {{ g.peak_day.strftime("%A %-d %B") }}

{% endfor %}{% endif %}
{% if facts.pace.count %}
## Pace

The week's quickest movers and slowest burners, from its {{ facts.pace.count }} big stories ({{ facts.pace.big }} or more outlets).

{% if facts.pace.fastest %}- Fastest to spread: {{ story(facts.pace.fastest) }}. {{ facts.pace.big }} outlets in {{ facts.pace.fastest.to_big | hours }}.
{% endif %}{% if facts.pace.longest %}- Longest running: {{ story(facts.pace.longest) }}. Most of its coverage came over {{ facts.pace.longest.most_within | hours }}.
{% endif %}{% if facts.pace.shortest %}- Quickest to fade: {{ story(facts.pace.shortest) }}. Most of its coverage was out within {{ facts.pace.shortest.most_within | hours }}.
{% endif %}{% endif %}
{% set a = facts.attention %}{% if a.top %}
## Who got the attention

Share of the week's articles naming any of the {{ stocks|length }} companies we follow.

{% for r in a.top %}- [{{ r.stock.name | md }}]({{ site.url }}/stocks/{{ r.stock.slug }}.html) ({{ r.stock.symbol }}) {{ "%.1f"|format(r.share) }}% ({{ r.articles }} articles from {{ r.outlets }} outlets)
{% endfor %}
The top three took {{ a.top3_share|round|int }}% between them. US companies drew {{ a.markets.get("US", 0) }}% of the attention and UK companies {{ a.markets.get("UK", 0) }}%, with {{ a.uk_covered }} of the {{ a.uk_count }} UK companies we follow getting any coverage at all.
{% if a.quiet %}
{{ a.quiet|length }} companies got no coverage at all this week: {% for s in a.quiet %}{{ s.name | md }}{% if loop.revindex == 2 %} and {% elif not loop.last %}, {% endif %}{% endfor %}.
{% endif %}
*Ranked by news coverage, not by merit. Not a buy list.*
{% endif %}
{% if facts.movers and (facts.movers.up or facts.movers.down) %}
## Who gained and lost attention

Change in share of coverage against the week before.

{% for d in facts.movers.up %}- {{ d.stock.name | md }}: up from {{ "%.1f"|format(d.before) }}% to {{ "%.1f"|format(d.now) }}%
{% endfor %}{% for d in facts.movers.down %}- {{ d.stock.name | md }}: down from {{ "%.1f"|format(d.before) }}% to {{ "%.1f"|format(d.now) }}%
{% endfor %}{% endif %}
{% if facts.shares %}
## Where the coverage went

{% for x in facts.shares %}- {{ x.name }}: {{ x.share|round|int }}% of stories{% if x.previous_share is not none %} (week before: {{ x.previous_share|round|int }}%){% endif %}
{% endfor %}{% endif %}
{% set o = facts.outlets %}{% if o.on_big %}
## The outlets

We saw {{ "{:,}".format(o.article_count) }} articles on the companies we follow this week, from {{ "{:,}".format(o.outlet_count) }} outlets.{% if facts.days_load %} {{ facts.days_load[0].strftime("%A") }} was the busiest day with {{ "{:,}".format(facts.days_load[1]) }} articles, {{ facts.days_load[2].strftime("%A") }} the quietest with {{ "{:,}".format(facts.days_load[3]) }}.{% endif %}

The outlets on the most of the week's {{ o.big_count }} big stories:

{% for name, n in o.on_big %}- {{ name | md }}: {{ n }} of {{ o.big_count }}
{% endfor %}{% endif %}
{% if facts.results %}
## Results that drew the press

Companies that reported this week, by how many outlets covered their results. This counts every article on the results, so it can be higher than any one story's count.

{% for r in facts.results %}- {{ r.name | md }} ({{ r.symbol }}) {{ r.coverage }} outlets{% if r.filings %}. Filings: {% for f in r.filings %}[{{ f.label }} ({{ f.form }})]({{ f.url }}){% if not loop.last %}, {% endif %}{% endfor %}{% endif %}
{% endfor %}{% endif %}
{% if facts.coming %}
## Results next week

{% for c in facts.coming %}- {{ c.date.strftime("%A %-d %B") }}: {{ c.stock.name | md }} ({{ c.stock.symbol }})
{% endfor %}{% endif %}

*All counts are across the outlets All Tech Stocks tracks, not the whole press. Timings start from the first article we saw, which isn't necessarily the first one published.*

*Not financial advice. {{ disclaimer }}*

Tech stock news, updated every hour, at [{{ site.url | replace("https://", "") }}]({{ site.url }}).
