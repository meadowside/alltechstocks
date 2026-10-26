{#- The daily email as Markdown, for Andy to edit and paste into Substack.
    Kept simple on purpose: the Desktop "Copy for Substack" button converts exactly these
    pieces (## headings, - bullets, **bold**, *italic*, [links](url)) into formatting. -#}
{% macro tks(s) %}{% if s.tickers %} {{ s.tickers[:3]|join(", ") }}{% endif %}{% endmacro -%}
{% if titles %}TITLE IDEAS (pick one for the Substack title, then delete these lines. Cover 1, 2 and 3 match titles 1, 2 and 3)
{% for t in titles %}{{ loop.index }}. {{ t }}
{% endfor %}
---

{% endif %}All Tech Stocks, {{ date_long }}

{% if opening %}[Suggested opening note below. Edit it, then delete this line. Report, don't tip.]
{{ opening | md }}{% else %}[Your opening note: two or three sentences on what's big today. Report, don't tip.]{% endif %}

Andy
{% if top %}
## Top story

**[{{ top.headline | md }}]({{ top.link }})**
{{ top.link_outlet.name }}{% if top.paywall %} (£){% endif %}, {{ top.source_count }} source{{ 's' if top.source_count != 1 }}.{{ tks(top) }}

**Why it matters:** [Your line. Business impact only, nothing on the shares.{% if top.so_what %} AI suggests: {{ top.so_what | md }}{% endif %}]
{% endif %}
{% for sec in sections %}{% if sec.stories %}{% set lead = sec.stories[0] %}
## {{ sec.name }}

**Andy's Pick: {% if lead.is_update %}Update: {% endif %}[{{ lead.headline | md }}]({{ lead.link }})** ({{ lead.link_outlet.name }}{% if lead.paywall %}, £{% endif %}{% if lead.source_count > 1 %}, {{ lead.source_count }} sources{% endif %}){{ tks(lead) }}{% if lead.so_what %}
*So what: {{ lead.so_what | md }}*{% endif %}
{% if sec.stories|length > 1 %}
{% for s in sec.stories[1:] %}- {% if s.is_update %}Update: {% endif %}[{{ s.headline | md }}]({{ s.link }}) ({{ s.link_outlet.name }}{% if s.paywall %}, £{% endif %}){{ tks(s) }}
{% endfor %}{% endif %}
{% endif %}{% endfor %}{% if ranked %}
## Most covered stocks

{% for r in ranked %}- **{{ r.stock.symbol }}** [{{ r.stock.name | md }}]({{ site.url }}/stocks/{{ r.stock.slug }}.html): {{ r.outlet_count }} outlets, {{ r.story_count }} {{ "story" if r.story_count == 1 else "stories" }}
{% endfor %}
*Ranked by news coverage, not by merit. Not a buy list.*
{% endif %}
{% if filings %}
## New US filings

{% for f in filings %}- **{{ f.symbol }}** {{ stocks[f.symbol].name | md }}: [{{ f.label }} ({{ f.form }})]({{ f.url }})
{% endfor %}
{% endif %}
*Not financial advice. {{ disclaimer }}*

More tech stock news, updated every hour, at [{{ site.url | replace("https://", "") }}]({{ site.url }}).
