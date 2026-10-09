// HR AI Agents: optional UI. Everything below is inert unless the server put
// frappe.boot.hr_ai_agents on the page (app enabled + user has AI Agent User + not opted out).
(function () {
  const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));
  const call = (method, args) =>
    frappe.call({ method: "hr_ai_agents.hr_ai_agents.api." + method, args: args || {}, freeze: false }).then((r) => r.message);

  // ---------- ATS panel ----------
  function chips(list, cls) {
    return (list || []).map((x) => {
      const t = typeof x === "string" ? x : x.term + (x.via ? " (" + x.via + ")" : "");
      const c = typeof x !== "string" && x.type === "semantic" ? "ai-chip-sem" : cls;
      const title = typeof x !== "string" && x.evidence ? ' title="' + esc(x.evidence) + '"' : "";
      return '<span class="ai-chip ' + c + '"' + title + ">" + esc(t) + "</span>";
    }).join("") || '<span class="ai-muted">none</span>';
  }
  function atsHtml(r) {
    const m = r.matched || {}, x = r.missing || {};
    const items = (t) => String(t || "").split("\n").map((l) => l.trim()).filter(Boolean);
    const list = (t, ordered) => { const a = items(t); return a.length ? "<" + (ordered ? "ol" : "ul") + ' class="ai-list">' + a.map((l) => "<li>" + esc(l.replace(/^[-*\u2022]\s*/, "")) + "</li>").join("") + "</" + (ordered ? "ol" : "ul") + ">" : ""; };
    const card = (title, body, cls) => body ? '<div class="ai-card ' + (cls || "") + '"><div class="ai-card-title">' + title + "</div>" + body + "</div>" : "";
    const unreadable = /No readable CV text/i.test(r.risk_flags || "");
    const when = r.scored_on ? esc(String(r.scored_on).split(".")[0].replace("T", " ")) : "";
    const pct = Math.max(0, Math.min(100, Math.round(r.score || 0)));
    const count = (a, b2) => a.length + " of " + (a.length + b2.length);
    const reqBlock = (label, got, miss) => {
      const total = (got || []).length + (miss || []).length;
      if (!total) return "";
      return '<div class="ai-req"><div class="ai-req-head"><b>' + label + '</b><span class="ai-muted">' + count(got || [], miss || []) + " matched</span></div>" +
        '<div class="ai-req-row"><span class="ai-req-tag ok">Matched</span><div>' + chips(got, "ai-chip-ok") + "</div></div>" +
        '<div class="ai-req-row"><span class="ai-req-tag miss">Missing</span><div>' + chips(miss, "ai-chip-miss") + "</div></div></div>";
    };
    const bd = (r.breakdown || []).map((b2) => {
      const p = b2.ratio == null ? null : Math.round(b2.ratio * 100);
      return "<tr><td>" + esc(String(b2.name).replace(/_/g, " ")) + "</td><td class='ai-bar-cell'>" +
        (p == null ? '<span class="ai-muted">not assessable</span>' : '<div class="ai-bar"><div style="width:' + p + '%"></div></div><span>' + p + "%</span>") +
        "</td><td class='ai-num'>" + (b2.points == null ? "" : b2.points + " pts") + "</td></tr>";
    }).join("");
    const flags = items(r.risk_flags).filter((f) => !/No readable CV text/i.test(f));
    return '<div class="ai-ats2">' +
      '<div class="ai-ats-top"><div class="ai-ring ' + (unreadable ? "ai-ring-off" : "") + '" style="--p:' + pct + '"><div><b>' + esc(r.score) + '</b><span>/ 100</span></div></div>' +
      '<div class="ai-ats-meta"><div><span class="ai-band ai-band-' + esc(r.band) + '">' + esc(r.band) + '</span> <span class="ai-muted">Advisory only: a person decides.</span></div>' +
      '<div class="ai-muted">Scored by Meera' + (r.model ? " with AI summary (" + esc(r.model) + ")" : ", keyword match only") + (when ? " &middot; " + when : "") + " &middot; criteria v" + esc(r.criteria_version) + "</div></div></div>" +
      (unreadable ? '<div class="ai-warn"><b>The CV could not be read.</b> ' + esc(items(r.risk_flags).filter((f) => !/^No readable CV text/i.test(f))[0] || "No text was found in the attached resume, cover letter or notes.") +
        " This score is not meaningful until a readable CV is attached.</div>" : "") +
      '<div class="ai-grid">' +
        card("Must-have skills", reqBlock("Required", m.must_have, x.must_have)) +
        card("Nice-to-have skills", reqBlock("Preferred", m.nice_have, x.nice_have)) +
      "</div>" +
      card("Work profile", r.summary ? '<div class="ai-prose">' + esc(r.summary).split(/\n+/).map((l) => "<p>" + l + "</p>").join("") + "</div>" : "") +
      '<div class="ai-grid">' + card("Strengths", list(r.strengths), "ai-card-good") + card("Gaps", list(r.gaps), "ai-card-gap") + "</div>" +
      card("Suggested interview questions", list(r.interview_questions, true)) +
      (!unreadable && flags.length ? card("Flags to check", list(flags.join("\n")), "ai-card-flag") : "") +
      card("Score breakdown", "<table class='ai-bd'>" + bd + "</table>") +
      "</div>";
  }
  function showAts(frm, r) {
    frm.dashboard.clear_headline && frm.dashboard.clear_headline();
    if (frm.__ai_ats_section) frm.__ai_ats_section.remove();
    frm.__ai_ats_section = frm.dashboard.add_section(atsHtml(r), __("AI Resume Match"));
  }
  function runAts(frm) {
    frappe.show_progress(__("Meera is reading the CV"), 40, 100, __("Matching against the Job Opening"));
    call("ats_score", { applicant: frm.doc.name }).then((r) => {
      frappe.hide_progress();
      if (!r || r.error) return frappe.msgprint({ title: __("AI Resume Match"), message: esc((r && r.error) || "Failed"), indicator: "orange" });
      showAts(frm, r);
      frappe.show_alert({ message: __("Score {0}/100 ({1})", [r.score, r.band]), indicator: "green" });
    }).catch(() => frappe.hide_progress());
  }

  // ---------- completeness / anomalies ----------
  function findingsHtml(list) {
    if (!list || !list.length) return "<p>No issues found.</p>";
    const pill = (sev) => '<span class="indicator-pill ' + (sev === "High" ? "red" : sev === "Medium" ? "orange" : "gray") + '">' + esc(sev) + "</span>";
    return '<div class="ai-table-wrap"><table class="ai-table"><thead><tr><th style="width:90px">' + __("Severity") + "</th><th>" + __("Finding") + "</th><th>" + __("Why it matters") + "</th><th>" + __("Suggested action") + "</th></tr></thead><tbody>" +
      list.map((f) => "<tr><td>" + pill(f.severity) + "</td><td><b>" + esc(f.message) + "</b></td><td>" + (esc(f.impact) || '<span class="ai-muted">-</span>') + "</td><td>" + (esc(f.suggested_fix) || '<span class="ai-muted">-</span>') + "</td></tr>").join("") +
      "</tbody></table></div>";
  }
  function runCheck(frm) {
    frappe.show_alert({ message: __("Sentinel is reviewing this document"), indicator: "blue" });
    call("check_document", { doctype: frm.doctype, name: frm.doc.name }).then((r) => {
      if (!r || r.error) return frappe.msgprint({ title: __("Document review"), message: esc((r && r.error) || "Failed"), indicator: "orange" });
      frappe.msgprint({ title: __("Sentinel review: {0} finding(s)", [r.findings.length]), message: findingsHtml(r.findings), wide: true });
    });
  }

  // ---------- proposals (review and apply by a person) ----------
  function proposalBody(p) {
    const meta = '<div class="ai-muted">Prepared by ' + esc(p.agent) + " &middot; " + esc(p.creation || "") + (p.expires_on ? " &middot; expires " + esc(p.expires_on) : "") + "</div>";
    const why = p.rationale ? "<p><b>" + __("Why") + "</b><br>" + esc(p.rationale) + "</p>" : "";
    const body = p.operation === "Draft text"
      ? '<pre class="ai-draft-text">' + esc(p.text || "") + "</pre>"
      : "<p class='ai-muted'>" + __("Nothing has been saved yet. Applying creates or changes a Draft using your own permissions, validation rules and workflow. Nothing is submitted or approved.") + "</p>" + (p.diff_html || "");
    return meta + why + body;
  }
  function openProposal(name, onDone) {
    call("get_proposal", { name }).then((p) => {
      if (!p) return;
      const pending = p.status === "Pending";
      const d = new frappe.ui.Dialog({
        title: p.title || __("AI proposal"),
        fields: [{ fieldname: "body", fieldtype: "HTML" }],
        primary_action_label: !pending ? __("Close") : p.operation === "Draft text" ? __("Copy text") : __("Apply changes"),
        primary_action() {
          if (!pending) return d.hide();
          if (p.operation === "Draft text") {
            frappe.utils.copy_to_clipboard(p.text || "");
            return call("apply_proposal", { name }).then(() => { d.hide(); onDone && onDone(); });
          }
          d.get_primary_btn().prop("disabled", true).text(__("Applying..."));
          call("apply_proposal", { name }).then((r) => {
            d.hide();
            onDone && onDone();
            if (r && r.status === "Applied") {
              frappe.show_alert({ message: __("Applied"), indicator: "green" });
              if (r.doctype) frappe.set_route("Form", r.doctype, r.name);
              else if (p.reference_doctype && p.reference_name) frappe.set_route("Form", p.reference_doctype, p.reference_name);
            } else {
              frappe.msgprint({ title: __("Could not apply"), message: esc((r && r.error) || "Failed"), indicator: "orange" });
            }
          });
        },
        secondary_action_label: pending ? __("Reject") : null,
        secondary_action() {
          frappe.prompt([{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason (optional)") }], (v) => call("reject_proposal", { name, reason: v.reason }).then(() => { d.hide(); onDone && onDone(); frappe.show_alert(__("Rejected")); }), __("Reject proposal"));
        },
      });
      d.get_field("body").$wrapper.html(proposalBody(p) + (!pending ? '<p><span class="indicator-pill gray">' + esc(p.status) + "</span> " + esc(p.error || "") + "</p>" : ""));
      d.show();
    });
  }
  function openProposalList() {
    call("my_proposals", { status: "Pending" }).then((rows) => {
      const d = new frappe.ui.Dialog({ title: __("Proposals waiting for your review"), fields: [{ fieldname: "list", fieldtype: "HTML" }] });
      const html = (rows || []).map((r) => '<div class="ai-prop-row"><a href="#" data-n="' + esc(r.name) + '"><b>' + esc(r.title) + '</b></a><div class="ai-muted">' + esc(r.agent) + " &middot; " + esc(r.operation) + " &middot; " + esc(r.creation) + "</div></div>").join("") || "<p class='ai-muted'>" + __("Nothing waiting.") + "</p>";
      d.get_field("list").$wrapper.html(html).find("a").on("click", (e) => { e.preventDefault(); d.hide(); openProposal($(e.currentTarget).data("n"), refreshBadge); });
      d.show();
    });
  }
  function refreshBadge() {
    call("my_proposals", { status: "Pending" }).then((rows) => {
      const n = (rows || []).length;
      $(".ai-prop-fab").remove();
      if (n) $('<button class="btn btn-warning ai-prop-fab">' + __("AI proposals") + " (" + n + ")</button>").appendTo("body").on("click", openProposalList);
    });
  }
  function formProposalBanner(frm) {
    if (frm.is_new()) return;
    call("proposals_for", { doctype: frm.doctype, name: frm.doc.name }).then((list) => {
      if (!list || !list.length) return;
      frm.dashboard.set_headline_alert(__("{0} AI proposal(s) waiting for review on this record", [list.length]) + ' - <a href="#" class="ai-open-p">' + __("review") + "</a>", "blue");
      setTimeout(() => $(".ai-open-p").off("click").on("click", (e) => { e.preventDefault(); openProposal(list[0].name, () => frm.reload_doc()); }), 100);
    });
  }

  // ---------- Ask an agent ----------
  function filesHtml(files) {
    if (!files || !files.length) return "";
    const icon = (f) => (f.format === "excel" ? "Excel" : "Word");
    return '<div class="ai-files">' + files.map((f) =>
      '<a class="btn btn-sm btn-default ai-download" href="' + esc(f.url) + '" download="' + esc(f.file_name) + '" target="_blank">' +
      __("Download {0}", [icon(f)]) + " (" + esc(f.file_name) + ", " + Math.max(1, Math.round((f.size || 0) / 1024)) + " KB)</a>").join(" ") + "</div>";
  }

  function openHistory(onPick) {
    let start = 0;
    const d = new frappe.ui.Dialog({ title: __("My requests to AI agents"), size: "large", fields: [{ fieldname: "list", fieldtype: "HTML" }], primary_action_label: __("Close"), primary_action() { d.hide(); } });
    const body = () => d.get_field("list").$wrapper;
    const row = (r) => '<div class="ai-hist-row"><div class="ai-hist-head"><span class="ai-band ai-band-Good">' + esc(r.agent) + '</span> <span class="ai-muted">' + esc(frappe.datetime.str_to_user(String(r.when).split(".")[0])) +
      " &middot; " + esc(r.outcome || "") + (r.tokens ? " &middot; " + r.tokens + " tokens" : "") + "</span>" +
      (r.stored ? ' <a href="#" class="ai-hist-again pull-right" data-run="' + esc(r.run) + '">' + __("Ask again") + "</a>" : "") + "</div>" +
      (r.stored ? '<div class="ai-hist-prompt">' + esc(r.prompt) + "</div>" + (r.response ? '<details><summary class="ai-muted">' + __("Answer") + '</summary><div class="ai-hist-answer">' + frappe.markdown(r.response) + "</div></details>" : "") : '<div class="ai-muted">' + __("Text is not stored (hash only setting).") + "</div>") + "</div>";
    const load = () => call("my_history", { start, limit: 20 }).then((res) => {
      const rows = (res && res.rows) || [];
      if (!start) body().html('<div class="ai-muted" style="margin-bottom:8px">' + __("Only your own requests are listed. Emails, phone numbers and IDs may appear masked.") + '</div><div class="ai-hist"></div>');
      const box = body().find(".ai-hist");
      box.find(".ai-hist-more").remove();
      if (!rows.length && !start) box.html('<div class="ai-muted">' + __("No requests yet.") + "</div>");
      rows.forEach((r) => { const el = $(row(r)); el.find(".ai-hist-again").on("click", (e) => { e.preventDefault(); d.hide(); onPick && onPick(r.prompt, r.agent); }); box.append(el); });
      start += 20;
      if (res && res.more) box.append($('<button class="btn btn-default btn-sm ai-hist-more">' + __("Load more") + "</button>").on("click", load));
    });
    d.show(); load();
  }

  function openAsk(prefill, prefillAgent) {
    const info = frappe.boot.hr_ai_agents;
    const route = frappe.get_route();
    const ctx = route[0] === "Form" ? { doctype: route[1], name: route[2] } : {};
    let skipFaq = 0;
    const d = new frappe.ui.Dialog({
      title: __("Ask an agent"),
      size: "extra-large",
      fields: [
        { fieldname: "agent", fieldtype: "Select", label: __("Agent"), options: info.agents.map((a) => a.name).join("\n"), default: prefillAgent || (info.agents[0] || {}).name, reqd: 1 },
        { fieldname: "ai_cb", fieldtype: "Column Break" },
        { fieldname: "about", fieldtype: "HTML" },
        { fieldname: "ai_sb", fieldtype: "Section Break" },
        { fieldname: "quick", fieldtype: "HTML" },
        { fieldname: "prompt", fieldtype: "Text", label: __("What do you need?"), reqd: 1, default: prefill || "" },
        { fieldname: "suggest", fieldtype: "HTML" },
        { fieldname: "answer", fieldtype: "HTML" },
      ],
      primary_action_label: __("Ask"),
      primary_action(v) { doAsk(v.agent, v.prompt, 0); },
      secondary_action_label: __("Turn AI off for me"),
      secondary_action() {
        frappe.confirm(__("Hide AI agents for your account? You can ask an administrator to turn it back on."), () => call("set_opt_out", { opted_out: 1 }).then(() => { d.hide(); $(".ai-fab").remove(); frappe.show_alert(__("AI agents are off for you")); }));
      },
    });
    const box = () => d.get_field("answer").$wrapper;
    const busy = (on) => d.get_primary_btn().prop("disabled", on).text(on ? __("Working...") : __("Ask"));

    function feedbackLinks(r) {
      box().find(".ai-up").on("click", (e) => { e.preventDefault(); call("feedback", { run: r.run, rating: "Up" }).then((x) => frappe.show_alert(x && x.candidate ? __("Thanks. Sent to HR as a candidate FAQ.") : __("Thanks"))); });
      box().find(".ai-down").on("click", (e) => { e.preventDefault(); frappe.prompt([{ fieldname: "note", fieldtype: "Small Text", label: __("What was wrong?") }], (v2) => call("feedback", { run: r.run, rating: "Down", note: v2.note }), __("Feedback")); });
    }
    function showAnswer(r) {
      const b = box();
      if (!r || r.error) { b.html('<div class="ai-answer" style="color:#a01b1b">' + esc((r && r.error) || "Failed") + "</div>"); return; }
      let foot;
      if (r.served_from === "faq") {
        foot = '<div class="ai-source ai-source-faq">' + __("From the knowledge base") + (r.faq && r.faq.verified_on ? ", " + __("verified on {0}", [esc(r.faq.verified_on)]) : "") + (r.faq && r.faq.approved_by ? " " + __("by {0}", [esc(r.faq.approved_by)]) : "") +
          ". " + __("No AI tokens used.") + ' <a href="#" class="ai-anyway">' + __("Ask the AI anyway") + '</a> &middot; <a href="#" class="ai-up">' + __("Helpful") + '</a> / <a href="#" class="ai-down">' + __("Not helpful") + "</a></div>";
      } else if (r.served_from === "quick_action") {
        foot = '<div class="ai-source ai-source-live">' + __("Live data from the system under your own access. No AI used.") + "</div>";
      } else {
        foot = '<div class="ai-muted" style="margin-top:6px">AI-generated. Check before acting. ' + r.tokens + " tokens. " + '<a href="#" class="ai-up">' + __("Helpful") + '</a> / <a href="#" class="ai-down">' + __("Not helpful") + "</a></div>";
      }
      b.html((r.title ? "<h6>" + esc(r.title) + "</h6>" : "") + '<div class="ai-answer">' + frappe.markdown(r.answer || "") + "</div>" + filesHtml(r.files) + foot);
      if (r.proposals && r.proposals.length) {
        b.append('<div class="ai-prop-made">' + r.proposals.map((n) => '<button class="btn btn-sm btn-primary ai-prop-open" data-n="' + esc(n) + '">' + __("Review proposal {0}", [esc(n)]) + "</button>").join(" ") + "</div>");
        b.find(".ai-prop-open").on("click", (e) => { d.hide(); openProposal($(e.currentTarget).data("n"), refreshBadge); });
        refreshBadge();
      }
      b.find(".ai-anyway").on("click", (e) => { e.preventDefault(); doAsk(d.get_value("agent"), d.get_value("prompt"), 1); });
      feedbackLinks(r);
    }
    function doAsk(agent, prompt, skip) {
      if (!prompt) return frappe.show_alert({ message: __("Type your question first"), indicator: "orange" });
      busy(true);
      call("ask", { agent, prompt, context: JSON.stringify(ctx), skip_faq: skip ? 1 : 0 }).then((r) => { busy(false); showAnswer(r); }).catch(() => busy(false));
    }
    function runQuick(a) {
      const go = (params) => { busy(true); call("run_quick_action", { name: a.name, params: JSON.stringify(params || {}) }).then((r) => { busy(false); showAnswer(r); }).catch(() => busy(false)); };
      if (a.parameters && a.parameters.length) frappe.prompt(a.parameters.map((p) => ({ fieldname: p.name, fieldtype: p.fieldtype || "Data", label: p.label || p.name, options: p.options, reqd: p.reqd ? 1 : 0 })), (vals) => go(vals), a.title, __("Show"));
      else go({});
    }
    function loadQuick() {
      const agent = d.get_value("agent");
      call("quick_list", { agent }).then((q) => {
        const w = d.get_field("quick").$wrapper;
        q = q || {};
        const acts = (q.actions || []).map((a) => '<button class="btn btn-xs btn-default ai-chip-btn ai-chip-live" data-n="' + esc(a.name) + '" title="' + esc(a.description || __("Live data, no AI")) + '">&#9889; ' + esc(a.title) + "</button>");
        const fq = (q.faqs || []).map((f) => '<button class="btn btn-xs btn-default ai-chip-btn ai-chip-faq" data-n="' + esc(f.name) + '" data-q="' + esc(f.question) + '">' + esc(f.question) + "</button>");
        w.html(acts.length || fq.length ? '<div class="ai-quick"><div class="ai-muted">' + __("Quick questions (answered without AI tokens)") + "</div>" + acts.join("") + fq.join("") + "</div>" : "");
        w.find(".ai-chip-live").on("click", (e) => runQuick((q.actions || []).find((a) => a.name === $(e.currentTarget).data("n"))));
        w.find(".ai-chip-faq").on("click", (e) => { const t = $(e.currentTarget).attr("data-q"); d.set_value("prompt", t); doAsk(d.get_value("agent"), t, 0); });
      });
    }
    let sugTimer = null;
    function suggest() {
      clearTimeout(sugTimer);
      sugTimer = setTimeout(() => {
        const text = (d.get_value("prompt") || "").trim();
        const w = d.get_field("suggest").$wrapper;
        if (text.length < 5) return w.html("");
        call("faq_suggest", { agent: d.get_value("agent"), text }).then((list) => {
          w.html(list && list.length ? '<div class="ai-suggest"><span class="ai-muted">' + __("Already answered:") + "</span> " + list.map((f) => '<a href="#" class="ai-sug" data-q="' + esc(f.question) + '">' + esc(f.question) + "</a>").join(" &middot; ") + "</div>" : "");
          w.find(".ai-sug").on("click", (e) => { e.preventDefault(); const t = $(e.currentTarget).attr("data-q"); d.set_value("prompt", t); doAsk(d.get_value("agent"), t, 0); });
        });
      }, 450);
    }
    const setAbout = () => { const a = info.agents.find((x) => x.name === d.get_value("agent")) || {}; d.get_field("about").$wrapper.html('<div class="ai-muted">' + esc(a.title || "") + (a.can_do ? " - " + esc(a.can_do) : "") + "</div>"); loadQuick(); };
    d.fields_dict.agent.df.onchange = setAbout;
    d.$wrapper.addClass("ai-ask-dialog");
    d.add_custom_action(__("My history"), () => { d.hide(); openHistory((prompt, agent) => openAsk(prompt, agent)); }, "btn-default");
    d.show(); setAbout();
    d.fields_dict.prompt.$input && d.fields_dict.prompt.$input.on("input", suggest);
  }

  function init() {
    const info = frappe.boot && frappe.boot.hr_ai_agents;
    if (!info || window.__hr_ai_init) return;
    window.__hr_ai_init = 1;
    if (!$(".ai-fab").length) $('<button class="btn btn-primary ai-fab">' + __("Ask AI") + "</button>").appendTo("body").on("click", () => openAsk());

    refreshBadge();
    (info.proposal_doctypes || []).forEach((dt) => {
      frappe.ui.form.on(dt, { refresh(frm) { formProposalBanner(frm); } });
    });
    frappe.ui.form.on("AI Proposal", {
      refresh(frm) {
        if (frm.doc.status === "Pending") frm.add_custom_button(__("Review and apply"), () => openProposal(frm.doc.name, () => frm.reload_doc()));
      },
    });
    frappe.ui.form.on("AI FAQ", {
      refresh(frm) {
        if (frm.is_new() || !(frappe.user_roles || []).some((r) => ["HR Manager", "System Manager"].includes(r))) return;
        const act = (action, label, group) => frm.add_custom_button(label, () => call("faq_review", { name: frm.doc.name, action }).then(() => frm.reload_doc()), group);
        if (["Candidate", "Needs review"].includes(frm.doc.status)) {
          if (frm.doc.status === "Candidate") act("approve", __("Approve"), __("Review"));
          else act("verify", __("Verify and re-approve"), __("Review"));
          act("reject", __("Reject"), __("Review"));
        }
        if (frm.doc.status === "Approved") { act("verify", __("Mark verified today"), __("Review")); act("retire", __("Retire"), __("Review")); }
        if (frm.doc.status === "Candidate") frm.dashboard.set_headline(__("Candidate FAQ. Check the answer has no personal data, choose who can see it under 'Visible to roles', then Approve."), "orange");
      },
    });
    frappe.ui.form.on("AI Daily Report", {
      refresh(frm) {
        if (frm.is_new()) return;
        [["word", __("Word (.docx)")], ["excel", __("Excel (.xlsx)")]].forEach(([fmt, label]) =>
          frm.add_custom_button(label, () => call("export_daily_report", { report: frm.doc.name, format: fmt }).then((r) => {
            if (!r || r.error) { frappe.msgprint(esc((r && r.error) || __("Export failed"))); return; }
            window.open(r.url, "_blank");
            frm.reload_doc();
          }), __("Download")));
      },
    });
    frappe.ui.form.on("AI Data Subject Request", {
      refresh(frm) {
        if (frm.is_new() || !(info.is_auditor || info.is_manager)) return;
        frm.add_custom_button(__("Build data inventory"), () => call("build_dsr_inventory", { name: frm.doc.name }).then(() => frm.reload_doc()));
      },
    });
    frappe.ui.form.on("Job Applicant", {
      refresh(frm) {
        if (frm.is_new()) return;
        frm.add_custom_button(__("AI Resume Match"), () => runAts(frm), __("AI"));
        call("ats_latest", { applicant: frm.doc.name }).then((r) => { if (r) showAts(frm, r); });
      },
    });
    (info.check_doctypes || []).forEach((dt) => {
      frappe.ui.form.on(dt, {
        refresh(frm) {
          if (frm.is_new()) return;
          frm.add_custom_button(__("Check completeness"), () => runCheck(frm), __("AI"));
          call("open_findings", { doctype: frm.doctype, name: frm.doc.name }).then((list) => {
            if (list && list.length) frm.dashboard.set_headline_alert(__("{0} open AI finding(s) on this document", [list.length]) + ' - <a href="#" class="ai-show-f">' + __("view") + "</a>", "orange");
            if (list && list.length) setTimeout(() => $(".ai-show-f").off("click").on("click", (e) => { e.preventDefault(); frappe.msgprint({ title: __("Open findings"), message: findingsHtml(list), wide: true }); }), 100);
          });
        },
      });
    });
    frappe.ui.form.on("SLA Alert Log", {
      refresh(frm) {
        if (frm.doc.status === "Pending Review") frm.add_custom_button(__("Send now"), () => call("send_sla_alert", { alert: frm.doc.name }).then(() => frm.reload_doc()));
      },
    });
    if (info.is_auditor || info.is_manager) {
      frappe.ui.form.on("AI Agent Settings", {
        refresh(frm) {
          if (info.is_auditor) {
            frm.add_custom_button(__("Run audit scan now"), () => call("run_audit_now").then((r) => frappe.msgprint(r && r.error ? esc(r.error) : __("{0} observation(s). New findings are under AI Finding.", [r.observations]))), __("Governance"));
            frm.add_custom_button(__("Build monthly compliance pack"), () => call("build_pack_now").then((r) => r && frappe.set_route("Form", "AI Compliance Pack", r.pack)), __("Governance"));
            frm.add_custom_button(__("Run evaluation cases"), () => call("run_evals", { background: 1 }).then(() => frappe.msgprint(__("Evaluation started. Open AI Eval Run in a minute."))), __("Governance"));
          }
          if (info.is_manager) {
            frm.add_custom_button(__("Retention: preview and purge"), () => call("retention_preview").then((p) => {
              const rows = Object.keys(p.counts).map((k) => "<li>" + esc(k) + ": <b>" + p.counts[k] + "</b></li>").join("");
              frappe.confirm("<p>" + __("Records created before {0}:", [esc(p.older_than)]) + "</p><ul>" + rows + "</ul><p>" + __("Purging permanently deletes them. Audit logs are never purged. This action is logged. Continue?") + "</p>",
                () => call("retention_purge", { confirm: 1 }).then((r) => frappe.msgprint(__("Deleted: {0}", [esc(JSON.stringify(r.deleted))]))));
            }), __("Governance"));
          }
        },
      });
    }
    if (info.is_manager) {
      frappe.ui.form.on("AI Agent Settings", {
        refresh(frm) {
          frm.add_custom_button(__("Verify run-log integrity"), () => call("verify_log_chain", { doctype: "AI Agent Run Log" }).then((r) => frappe.msgprint(r.ok ? __("Chain intact: {0} rows checked.", [r.checked]) : __("Mismatch at {0}", [r.first_mismatch]))), __("Tools"));
          frm.add_custom_button(__("Check agent permissions"), () => call("diagnose_permissions").then((rows) => frappe.msgprint({ title: __("Agent permission check"), wide: true, message: '<div class="ai-table-wrap"><table class="ai-table"><thead><tr><th>' + __("Agent") + "</th><th>" + __("Roles") + "</th><th>" + __("Result") + "</th></tr></thead><tbody>" + rows.map((r) => "<tr><td><b>" + esc(r.agent) + "</b></td><td>" + esc(r.roles.join(", ")) + "</td><td>" + (r.cannot_read.length ? '<span style="color:#a01b1b">' + __("Cannot read {0}. Add a role under Additional roles on the agent profile.", [esc(r.cannot_read.join(", "))]) + "</span>" : '<span style="color:#0b6b36">OK</span>') + "</td></tr>").join("") + "</tbody></table></div>" })), __("Tools"));
          frm.add_custom_button(__("Generate status report now"), () => call("run_report_now").then((r) => r && (r.error ? frappe.msgprint(esc(r.error)) : frappe.set_route("Form", "AI Daily Report", r.report))), __("Tools"));
        },
      });
    }
  }
  $(document).on("startup", init);
  if (frappe.boot) setTimeout(init, 0);
})();
