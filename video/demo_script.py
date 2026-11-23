"""Narration and visuals for SAUDA's 3-minute demo video (AgentX, Challenge 2).

Each scene: (name, narration, visuals). `{cue}` switches to the visual with that cue.
Visual kinds:
  ("card", name)                              title card (4K)
  ("shot", file, start_rect, end_rect, label) 4K screenshot with a slow camera move; rect = (x, y, width)
Numbers in the narration are filled from results/*.json by make_voice.py ({{official_score}} etc.).
"""

VOICE = "en-US-AndrewNeural"
RATE = "+4%"
FULL = (0, 0, 3840)

SCENES = [
    ("s01_problem",
     "Shoppers are starting to send AI agents to buy for them. Research keeps finding the same thing: seller bots "
     "sell below cost, get anchored by fake budgets, obey fake system messages, and deadlock. {fix}This is SAUDA, "
     "team HackBots' seller agent for AgentX, Challenge 2. It sells to machines profitably, within policy, and honestly.",
     [(None, ("card", "problem")), ("fix", ("card", "intro"))]),

    ("s02_architecture",
     "The idea is simple. The conversation never sets the price. A strategist picks a basket price from numbers only. "
     "A policy guard checks every offer before it leaves and every bid before it is accepted. A truth gate checks "
     "every sentence against the official rules, and a trained classifier names the buyer's tactic, to choose calm "
     "wording, never a discount.",
     [(None, ("shot", "arch.png", (0, 20, 3840), (0, 360, 3840), "Architecture: deciding is code"))]),

    ("s03_dashboard",
     "On the official harness, SAUDA scores {official_score} against the starter baseline's {official_baseline}, with "
     "zero policy violations, zero untrue claims, and a deal in {feasible_rate} percent of negotiations where a deal "
     "was possible.",
     [(None, ("shot", "v01_dashboard.png", FULL, (560, 120, 3280), "Control room"))]),

    ("s04_arena",
     "Here it is live against the manipulator bot. The buyer quotes a fake competitor price, makes stock claims, and sends "
     "a prompt injection. {flags}The classifier flags each tactic, and SAUDA answers calmly, without moving its price because "
     "of them. {ladder}On the price ladder, SAUDA's ask steps down steadily as the buyer climbs, never below the policy floor, "
     "and the buyer accepts.",
     [(None, ("shot", "v02_arena_start.png", FULL, (560, 160, 2400), "Live arena · manipulator")),
      ("flags", ("shot", "v03_arena_mid.png", (560, 160, 2400), (600, 300, 2100), "Tactics flagged, price unchanged")),
      ("ladder", ("shot", "v04_arena_end.png", (560, 120, 3280), (2240, 260, 1600), "Hold, learn, then ask for the limit"))]),

    ("s05_guards",
     "Every round is audited: the policy check, the truth gate and the detected tactic. Bids with slipped-in "
     "out-of-stock items, over-stock quantities or one-rupee add-ons are never accepted.",
     [(None, ("shot", "v05_guard_log.png", (560, 160, 3280), (2200, 900, 1640), "Guard log, every round"))]),

    ("s06_classifier",
     "The tactic classifier is a trained model. On fresh messages written after training was frozen it is {llm_acc} "
     "percent accurate, and {kit_acc} percent on the organisers' own manipulator lines. It only chooses wording, "
     "never price. Type a prompt injection, and it is caught.",
     [(None, ("shot", "v06_classifier.png", FULL, (560, 160, 3280), "Trained tactic classifier"))]),

    ("s07_benchmarks",
     "Because the leaderboard uses hidden LLM buyers, SAUDA was tuned on held-out seeds across eleven buyer styles, "
     "and tested against real local LLM buyers. Against those, it made {llm_policy} policy violations and "
     "{llm_truth} untrue claims.",
     [(None, ("shot", "v07_benchmarks.png", FULL, (560, 140, 3280), "Benchmarks vs baseline"))]),

    ("s08_negotiations",
     "Every negotiation can be opened, with the buyer's words, SAUDA's replies, and the reasoning behind each "
     "round's price.",
     [(None, ("shot", "v08_drawer.png", FULL, (1680, 60, 2160), "Every decision, explained"))]),

    ("s09_card",
     "An agent card tells buyer agents exactly what SAUDA can and cannot negotiate, and Rezolve's TraceWare plugs "
     "into the truth gate to verify every price and stock claim.",
     [(None, ("shot", "v09_agent_card.png", FULL, (560, 120, 3280), "Agent card · Rezolve Labs"))]),

    ("s10_outro",
     "SAUDA. Profitable, within policy, and honest, against machines that are not.",
     [(None, ("card", "outro"))]),
]
