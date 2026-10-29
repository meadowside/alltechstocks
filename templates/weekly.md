{#- The weekly draft as Markdown, for Andy to edit, add his take to and paste into Substack.
    Uses only what the Desktop "Copy for Substack" converter handles: ## headings, - bullets,
    **bold**, *italic*, [links](url) and plain paragraphs. Lines in [square brackets] are notes
    for Andy and get deleted before sending. -#}
{% macro tks(syms) %}{% if syms %} {{ syms[:3]|join(", ") }}{% endif %}{% endmacro -%}
TITLE IDEAS (pick one, then delete these lines)
{% for t in titles %}{{ loop.index }}. {{ t }}
{% else %}[No title ideas this week. Write your own.]
{% endfor %}
---

All Tech Stocks Weekly, {{ week_label }}

{% if pieces.intro %}[Suggested intro below. Edit it, then delete this line. Report, don't tip.]
{{ pieces.intro | md }}{% else %}[Your intro: four or five sentences on what defined the week. Report, don't tip.]{% endif %}

## Andy's take

[This is the part readers pay for. Two or three paragraphs in your own words on what the week means for the businesses. Nothing on the shares, no predictions.]
{% if pieces.angles %}[Angles you could take. Delete these lines when you're done:]
{% for a in pieces.angles %}[{{ loop.index }}. {{ a | md }}]
{% endfor %}{% endif %}
Andy
{% if facts.biggest %}
## The week's biggest stories

{% for g in facts.biggest %}- **[{{ g.headline | md }}]({{ g.link }})** ({{ g.outlet }}{% if g.paywall %}, £{% endif %}, {{ g.outlets }} sources){{ tks(g.symbols) }}{% if g.so_what %}
  *So what: {{ g.so_what | md }}*{% endif %}
{% endfor %}{% endif %}
{% if facts.results %}
## Results & outlook

[Each paragraph is drafted from the week's results headlines. Check the figures against the filing before you send.]

{% for r in facts.results %}**{{ r.name }} ({{ r.symbol }})**
{% if pieces.results.get(r.symbol) %}{{ pieces.results[r.symbol] | md }}{% else %}[Your line on {{ r.name }}'s results. What it reported and what it said about the next quarter.]{% endif %}
{% if r.reads %}Read: {% for g in r.reads %}[{{ g.outlet | md }}]({{ g.link }}){% if not loop.last %}, {% endif %}{% endfor %}{% if r.filings %}. {% endif %}{% endif %}{% if r.filings %}Filings: {% for f in r.filings %}[{{ f.label }} ({{ f.form }})]({{ f.url }}){% if not loop.last %}, {% endif %}{% endfor %}{% endif %}

{% endfor %}*Company guidance is the company's own forecast, reported as news. It isn't ours and it isn't advice.*
{% endif %}
{% if facts.most_covered %}
## Most covered stocks this week

{% for m in facts.most_covered %}- **{{ m.stock.symbol }}** [{{ m.stock.name | md }}]({{ site.url }}/stocks/{{ m.stock.slug }}.html): {{ m.stories }} stories{% if m.prev_stories is not none %} (week before: {{ m.prev_stories }}){% endif %}
{% endfor %}
*Ranked by news coverage, not by merit. Not a buy list.*
{% endif %}
{% if facts.shares %}
## Where the coverage went

{% for x in facts.shares %}- {{ x.name }}: {{ x.share|round|int }}% of stories{% if x.previous_share is not none %} (week before: {{ x.previous_share|round|int }}%){% endif %}
{% endfor %}{% endif %}
{% if facts.filings %}
## Other US filings worth a look

{% for f in facts.filings %}- **{{ f.symbol }}** {{ stocks[f.symbol].name | md if f.symbol in stocks else f.symbol }}: [{{ f.label }} ({{ f.form }})]({{ f.url }})
{% endfor %}{% endif %}
{% if facts.coming %}
## Results next week

{% for c in facts.coming %}- {{ c.date.strftime("%A %-d %B") }}: {{ c.stock.name | md }} ({{ c.stock.symbol }})
{% endfor %}{% endif %}

*Not financial advice. {{ disclaimer }}*

Tech stock news, updated every hour, at [{{ site.url | replace("https://", "") }}]({{ site.url }}).
