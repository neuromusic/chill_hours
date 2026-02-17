// D3.js two-panel chill hours chart
(function () {
  const W = 960, H = 520;
  const margin = { top: 40, right: 200, bottom: 30, left: 55 };
  const gap = 50; // vertical gap between panels
  const panelH = (H - margin.top - margin.bottom - gap) / 2;

  fetch("chart_data.json")
    .then((r) => r.json())
    .then(draw)
    .catch(() => {
      d3.select("#chart").append("p").style("color", "#999").style("font-style", "italic")
        .text("Could not load chart data.");
    });

  function draw(data) {
    const parseDate = d3.timeParse("%Y-%m-%d");
    const xlimLeft = parseDate(data.xlim_left);
    const xlimRight = parseDate(data.xlim_right);
    const lastObserved = parseDate(data.last_observed);

    const nightly = data.nightly.map((d) => ({
      date: parseDate(d.date),
      chill_hours: d.chill_hours,
      cumulative: d.cumulative,
    }));
    const historical = data.historical.map((d) => ({
      date: parseDate(d.date),
      min: d.min, p25: d.p25, median: d.median, p75: d.p75, max: d.max,
    }));

    const svg = d3.select("#chart")
      .append("svg")
      .attr("viewBox", `0 0 ${W} ${H}`)
      .attr("preserveAspectRatio", "xMidYMid meet");

    // Title
    svg.append("text")
      .attr("x", W / 2).attr("y", 22)
      .attr("text-anchor", "middle")
      .attr("font-size", 14).attr("font-weight", "bold")
      .text("Chill Hours \u2014 Winter 2025\u20132026");

    // Shared x scale
    const x = d3.scaleTime().domain([xlimLeft, xlimRight])
      .range([margin.left, W - margin.right]);

    // Panel 1: Daily bars
    const p1Top = margin.top;
    const yMaxBar = d3.max(nightly, (d) => d.chill_hours) || 1;
    const y1 = d3.scaleLinear().domain([0, yMaxBar * 1.1]).range([p1Top + panelH, p1Top]);

    const g1 = svg.append("g");

    // "Not yet observed" shading — panel 1
    const futureStart = d3.timeDay.offset(lastObserved, 1);
    if (futureStart < xlimRight) {
      g1.append("rect")
        .attr("x", x(futureStart)).attr("y", p1Top)
        .attr("width", x(xlimRight) - x(futureStart))
        .attr("height", panelH)
        .attr("fill", "#f0f0f0");
      g1.append("line")
        .attr("x1", x(futureStart)).attr("x2", x(futureStart))
        .attr("y1", p1Top).attr("y2", p1Top + panelH)
        .attr("stroke", "#aaaaaa").attr("stroke-width", 1)
        .attr("stroke-dasharray", "4,3").attr("opacity", 0.6);
      g1.append("text")
        .attr("x", (x(futureStart) + x(xlimRight)) / 2)
        .attr("y", p1Top + 14)
        .attr("text-anchor", "middle").attr("font-size", 9).attr("fill", "#999999")
        .text("Not yet observed");
    }

    // Bars
    const barW = Math.max(1, (x(xlimRight) - x(xlimLeft)) / 152 * 0.8);
    g1.selectAll("rect.bar").data(nightly).join("rect")
      .attr("class", "bar")
      .attr("x", (d) => x(d.date) - barW / 2)
      .attr("y", (d) => y1(d.chill_hours))
      .attr("width", barW)
      .attr("height", (d) => y1(0) - y1(d.chill_hours))
      .attr("fill", (d) => d.chill_hours > 0 ? "#4a90d9" : "#cccccc");

    // Y gridlines
    g1.selectAll("line.grid").data(y1.ticks(5)).join("line")
      .attr("x1", margin.left).attr("x2", W - margin.right)
      .attr("y1", (d) => y1(d)).attr("y2", (d) => y1(d))
      .attr("stroke", "#ddd").attr("stroke-width", 0.5);

    // Axes
    g1.append("g").attr("transform", `translate(0,${p1Top + panelH})`)
      .call(d3.axisBottom(x).ticks(0).tickSize(0)).select(".domain");
    g1.append("g").attr("transform", `translate(${margin.left},0)`)
      .call(d3.axisLeft(y1).ticks(5));

    // Panel 1 labels
    g1.append("text").attr("x", margin.left).attr("y", p1Top - 8)
      .attr("font-size", 11).attr("font-weight", "bold")
      .text("Daily Chill Hours (32\u201345\u00b0F, noon\u2013noon)");
    g1.append("text")
      .attr("transform", `translate(15,${p1Top + panelH / 2}) rotate(-90)`)
      .attr("text-anchor", "middle").attr("font-size", 10)
      .text("Chill Hours per Night");

    // Panel 2: Cumulative
    const p2Top = p1Top + panelH + gap;
    const yMaxCum = Math.max(
      d3.max(nightly, (d) => d.cumulative) || 0,
      d3.max(historical, (d) => d.max) || 0,
      d3.max(data.varieties, (d) => d.target) || 0
    ) * 1.15;
    const y2 = d3.scaleLinear().domain([0, yMaxCum]).range([p2Top + panelH, p2Top]);

    const g2 = svg.append("g");

    // "Not yet observed" shading — panel 2
    if (futureStart < xlimRight) {
      g2.append("rect")
        .attr("x", x(futureStart)).attr("y", p2Top)
        .attr("width", x(xlimRight) - x(futureStart))
        .attr("height", panelH)
        .attr("fill", "#f0f0f0");
      g2.append("line")
        .attr("x1", x(futureStart)).attr("x2", x(futureStart))
        .attr("y1", p2Top).attr("y2", p2Top + panelH)
        .attr("stroke", "#aaaaaa").attr("stroke-width", 1)
        .attr("stroke-dasharray", "4,3").attr("opacity", 0.6);
    }

    // Historical min/max fill
    if (historical.length) {
      const areaMinMax = d3.area()
        .x((d) => x(d.date)).y0((d) => y2(d.min)).y1((d) => y2(d.max));
      g2.append("path").datum(historical)
        .attr("d", areaMinMax).attr("fill", "#888888").attr("opacity", 0.08);

      // p25/p75 fill
      const area2575 = d3.area()
        .x((d) => x(d.date)).y0((d) => y2(d.p25)).y1((d) => y2(d.p75));
      g2.append("path").datum(historical)
        .attr("d", area2575).attr("fill", "#888888").attr("opacity", 0.2);

      // Median dashed line
      const medianLine = d3.line().x((d) => x(d.date)).y((d) => y2(d.median));
      g2.append("path").datum(historical)
        .attr("d", medianLine).attr("fill", "none")
        .attr("stroke", "#888888").attr("stroke-width", 1.5)
        .attr("stroke-dasharray", "5,3");
    }

    // Current season fill
    const cumArea = d3.area()
      .x((d) => x(d.date)).y0(y2(0)).y1((d) => y2(d.cumulative));
    g2.append("path").datum(nightly)
      .attr("d", cumArea).attr("fill", "#d94a4a").attr("opacity", 0.15);

    // Current season line
    const cumLine = d3.line().x((d) => x(d.date)).y((d) => y2(d.cumulative));
    g2.append("path").datum(nightly)
      .attr("d", cumLine).attr("fill", "none")
      .attr("stroke", "#d94a4a").attr("stroke-width", 2.5);

    // Variety threshold lines + annotations
    data.varieties.forEach((v) => {
      const color = v.met ? "#2d8632" : "#b8860b";
      const symbol = v.met ? "\u2714" : "\u2022";
      const yPos = y2(v.target);

      // Horizontal dotted line
      g2.append("line")
        .attr("x1", margin.left).attr("x2", W - margin.right)
        .attr("y1", yPos).attr("y2", yPos)
        .attr("stroke", color).attr("stroke-width", 1)
        .attr("stroke-dasharray", "2,3").attr("opacity", 0.7);

      // Right-side annotation
      let label = `${symbol} ${v.label} (${v.target} hrs)`;
      if (!v.met && v.probability != null) {
        label += ` \u2014 ${Math.round(v.probability * 100)}%`;
      }
      g2.append("text")
        .attr("x", W - margin.right + 6).attr("y", yPos)
        .attr("dy", "0.35em").attr("font-size", 8)
        .attr("font-weight", "bold").attr("fill", color)
        .text(label);
    });

    // Y gridlines
    g2.selectAll("line.grid").data(y2.ticks(5)).join("line")
      .attr("x1", margin.left).attr("x2", W - margin.right)
      .attr("y1", (d) => y2(d)).attr("y2", (d) => y2(d))
      .attr("stroke", "#ddd").attr("stroke-width", 0.5);

    // Axes
    g2.append("g").attr("transform", `translate(0,${p2Top + panelH})`)
      .call(
        d3.axisBottom(x).ticks(d3.timeMonth.every(1))
          .tickFormat(d3.timeFormat("%b '%y"))
      )
      .selectAll("text").attr("transform", "rotate(-45)").attr("text-anchor", "end");
    g2.append("g").attr("transform", `translate(${margin.left},0)`)
      .call(d3.axisLeft(y2).ticks(5));

    // Panel 2 labels
    g2.append("text").attr("x", margin.left).attr("y", p2Top - 8)
      .attr("font-size", 11).attr("font-weight", "bold")
      .text("Cumulative Chill Hours vs. Historical");
    g2.append("text")
      .attr("transform", `translate(15,${p2Top + panelH / 2}) rotate(-90)`)
      .attr("text-anchor", "middle").attr("font-size", 10)
      .text("Cumulative Chill Hours");
    g2.append("text")
      .attr("x", (margin.left + W - margin.right) / 2)
      .attr("y", p2Top + panelH + 40)
      .attr("text-anchor", "middle").attr("font-size", 10)
      .text("Date");

    // Legend (upper-left of panel 2)
    const legend = g2.append("g").attr("transform", `translate(${margin.left + 10},${p2Top + 10})`);
    const items = [];
    if (historical.length) {
      items.push({ label: "Historical range (1975\u20132024)", type: "rect", color: "#888888", opacity: 0.15 });
      items.push({ label: "25th\u201375th percentile", type: "rect", color: "#888888", opacity: 0.35 });
      items.push({ label: "Historical median", type: "dash", color: "#888888" });
    }
    items.push({ label: "2025\u201326 season", type: "line", color: "#d94a4a" });

    items.forEach((item, i) => {
      const yOff = i * 16;
      if (item.type === "rect") {
        legend.append("rect").attr("x", 0).attr("y", yOff - 5)
          .attr("width", 20).attr("height", 10)
          .attr("fill", item.color).attr("opacity", item.opacity);
      } else if (item.type === "dash") {
        legend.append("line").attr("x1", 0).attr("x2", 20)
          .attr("y1", yOff).attr("y2", yOff)
          .attr("stroke", item.color).attr("stroke-width", 1.5)
          .attr("stroke-dasharray", "5,3");
      } else {
        legend.append("line").attr("x1", 0).attr("x2", 20)
          .attr("y1", yOff).attr("y2", yOff)
          .attr("stroke", item.color).attr("stroke-width", 2.5);
      }
      legend.append("text").attr("x", 26).attr("y", yOff)
        .attr("dy", "0.35em").attr("font-size", 9)
        .text(item.label);
    });
  }
})();
