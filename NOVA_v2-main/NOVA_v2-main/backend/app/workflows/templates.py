"""Workflow templates: reusable checklists that guide the idea-to-product workflow.

A template is NOT a scripted chatbot. It provides:
- topics: decisions worth clarifying, in order of consequence (the first open one is asked first when no
  AI model is available; with a model, the model picks the most consequential open topic);
- research questions with the source categories to check (used for real searches, or listed as pending);
- guidance: authored, domain-independent principles that every draft must respect;
- a task skeleton used to propose implementation tasks when no model is available.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Topic:
    id: str
    question: str
    why: str
    feeds: tuple[str, ...]  # artifact kinds this decision informs
    options: tuple[str, ...] = ()
    required: bool = True


@dataclass(frozen=True)
class ResearchQuestion:
    question: str
    categories: tuple[str, ...]


@dataclass(frozen=True)
class TaskSeed:
    title: str
    description: str
    domain: str
    depends_on: tuple[int, ...] = ()  # indexes into the skeleton


@dataclass(frozen=True)
class Template:
    id: str
    label: str
    description: str
    topics: tuple[Topic, ...]
    research: tuple[ResearchQuestion, ...]
    guidance: dict[str, tuple[str, ...]] = field(default_factory=dict)
    tasks: tuple[TaskSeed, ...] = ()


_PROBLEM = Topic("problem", "In one or two sentences, what problem does this solve, and for whom is it painful today?",
                 "Everything else (scope, metrics, architecture) depends on a precise problem statement.",
                 ("feasibility", "requirements", "proposal"))
_USERS = Topic("users", "Who are the primary users, and what are they trying to get done when they use it?",
               "Users and their jobs decide the screens, the language and what 'good enough' means.",
               ("requirements", "user_stories", "ui_spec"))
_SUCCESS = Topic("success", "How will you know the first version succeeded? Name one or two measurable signals.",
                 "Without a success signal there is no way to prioritise or to validate.",
                 ("requirements", "implementation_plan"), required=False)
_CONSTRAINTS = Topic("constraints", "What constraints apply: budget, deadline, team skills, platforms, regulations?",
                     "Constraints rule options in or out before design starts.",
                     ("technology_choices", "implementation_plan", "risk_register"))

GENERAL = Template(
    id="general",
    label="General idea",
    description="Any idea: clarifies problem, users, scope and constraints, then produces a proposal.",
    topics=(_PROBLEM, _USERS,
            Topic("scope", "What must the first version do, and what is explicitly out of scope?",
                  "A small, explicit scope keeps the first delivery achievable.", ("requirements", "implementation_plan")),
            _CONSTRAINTS, _SUCCESS),
    research=(ResearchQuestion("Which existing solutions address this problem, and where do they fall short?",
                               ("competitor products and reviews", "industry reports", "user forums")),),
    tasks=(TaskSeed("Validate the problem with 3–5 target users", "Interview users; confirm the problem and the job to be done.", "research"),
           TaskSeed("Define the first-version scope", "Turn the requirements into a prioritised scope list.", "product", (0,)),
           TaskSeed("Build a prototype of the core flow", "Smallest end-to-end version of the main user journey.", "engineering", (1,)),
           TaskSeed("Test the prototype with users", "Observe users completing the core task; record issues.", "research", (2,))),
)

SOFTWARE = Template(
    id="software_product",
    label="Software product",
    description="Web, mobile or desktop application: adds platform, data, integrations, security and deployment.",
    topics=(_PROBLEM, _USERS,
            Topic("core_flow", "Walk me through the single most important thing a user does, step by step.",
                  "The core flow drives the screen inventory and the API.", ("ui_spec", "user_stories", "api_contract")),
            Topic("platform", "Which platforms must it run on first (web, Android, iOS, desktop), and must it work offline?",
                  "Platform and offline needs decide the technology stack.", ("technology_choices", "architecture"),
                  ("Web", "Android", "iOS", "Cross-platform mobile", "Desktop")),
            Topic("data", "What data does it store, who owns it, and is any of it personal or sensitive?",
                  "Data shapes the data model, privacy duties and security design.", ("data_model", "architecture", "risk_register")),
            Topic("integrations", "Which external systems, payments or APIs must it connect to?",
                  "Integrations are frequent sources of cost and delay.", ("architecture", "api_contract"), required=False),
            _CONSTRAINTS, _SUCCESS),
    research=(ResearchQuestion("Which existing products solve this, and what do users complain about in them?",
                               ("app store reviews", "competitor documentation", "user forums")),
              ResearchQuestion("Which frameworks and hosting options fit the chosen platforms and constraints?",
                               ("official framework documentation", "hosting provider pricing pages")),
              ResearchQuestion("Which legal or privacy requirements apply to the data collected?",
                               ("data-protection authority guidance", "platform store policies"))),
    guidance={"architecture": ("Prefer the simplest architecture that meets the requirements; justify every service boundary.",),
              "risk_register": ("Include security, privacy, delivery and adoption risks with an owner and a mitigation.",)},
    tasks=(TaskSeed("Set up the repository, CI and environments", "Repository, linting, tests and a deployable skeleton.", "engineering"),
           TaskSeed("Implement the data model and core API", "Entities and endpoints from the data model and API contract.", "engineering", (0,)),
           TaskSeed("Build the core user flow screens", "Screens from the UI inventory, connected to the API.", "engineering", (1,)),
           TaskSeed("Add authentication and privacy controls", "Accounts, permissions and data-handling rules.", "engineering", (1,)),
           TaskSeed("Usability test with target users", "Run the core flow with 5 users; record and fix blockers.", "research", (2,)),
           TaskSeed("Prepare release and monitoring", "Store listing or deployment, error tracking, rollback plan.", "operations", (3, 4))),
)

ML_VISION = Template(
    id="ml_vision_app",
    label="Image-recognition app",
    description="Apps that classify or detect things in camera photos: adds classes, data, models, inference, "
                "image quality and uncertainty.",
    topics=(_USERS,
            Topic("subject_scope", "Which subjects (for example plant species, objects or products) and which use case "
                  "should the first version support?", "A narrow subject scope is the biggest factor in achievable accuracy.",
                  ("requirements", "feasibility", "user_stories")),
            Topic("classes", "Which visible symptoms or classes must it recognise at launch (including 'no visible symptoms'), "
                  "should it suggest possible causes, and what should happen for anything outside those classes?",
                  "The class list defines the dataset, the model output and how the app handles unknowns.",
                  ("requirements", "data_model", "risk_register")),
            Topic("dataset", "What labelled images exist or can be collected, from which conditions, and who checks label quality?",
                  "Model quality is bounded by data quality and by how closely training photos match real use.",
                  ("feasibility", "implementation_plan", "risk_register")),
            Topic("inference", "Should recognition run on the phone, in the cloud, or both? Is offline use required?",
                  "This decides latency, privacy, cost and which model sizes are possible.",
                  ("architecture", "technology_choices"), ("On-device", "Cloud", "Hybrid")),
            Topic("model", "Is fine-tuning a pretrained model acceptable, and are there limits on model size or licence?",
                  "Pretrained models reduce data needs; licences and size limits can rule some out.",
                  ("technology_choices", "feasibility")),
            Topic("capture", "Which phones must be supported, and what photo guidance is acceptable (distance, lighting, framing)?",
                  "Camera variation and poor photos are a major source of wrong predictions.",
                  ("ui_spec", "requirements")),
            Topic("uncertainty", "When the model is unsure, what should the app do (show 'can't tell', ask for another photo, "
                  "suggest an expert)?", "How uncertainty is communicated protects users from acting on wrong results.",
                  ("ui_spec", "requirements", "risk_register")),
            Topic("cost_connectivity", "Must it work without internet, and what running cost per month is acceptable for "
                  "servers or cloud inference?", "Connectivity and budget decide between on-device and server inference.",
                  ("technology_choices", "architecture", "feasibility"), required=False),
            Topic("privacy", "Will photos or locations leave the phone or be stored? For how long, and with what consent?",
                  "Photos can contain personal data and location; storage decisions shape the backend.",
                  ("architecture", "data_model", "risk_register")),
            Topic("backend", "Does the first version need accounts, history, or a server-side database?",
                  "A backend adds cost and privacy duties; many first versions do not need one.",
                  ("architecture", "data_model", "api_contract"), required=False),
            Topic("validation", "How will accuracy be validated before release (held-out real-world photos, expert review, "
                  "per-class metrics)?", "Lab accuracy often drops on real-world photos; validation must reflect real use.",
                  ("implementation_plan", "risk_register")),
            _SUCCESS),
    research=(ResearchQuestion("Which public labelled image datasets cover the chosen classes, under which licences, and how "
                               "were the photos taken (lab vs field)?",
                               ("dataset cards on Kaggle and Hugging Face", "peer-reviewed papers (Google Scholar, arXiv)")),
              ResearchQuestion("Which pretrained image models are suitable for fine-tuning on the chosen classes and size limits?",
                               ("model cards (Hugging Face, TensorFlow Hub, PyTorch Hub)", "benchmark papers")),
              ResearchQuestion("What accuracy do published systems report on real-world (not lab) photos for similar tasks?",
                               ("peer-reviewed papers", "domain-shift and robustness studies")),
              ResearchQuestion("Which on-device inference runtimes fit the target phones, and what latency do they report?",
                               ("TensorFlow Lite / LiteRT, Core ML, ONNX Runtime Mobile documentation",)),
              ResearchQuestion("How do comparable apps communicate uncertainty and advise users to seek expert confirmation?",
                               ("app store listings and reviews", "domain expert or extension-service guidance"))),
    guidance={
        "*": ("A prediction from one photo estimates visible features only. Never present it as a definitive or complete "
              "assessment (for example, of health); always show confidence and an 'uncertain' outcome.",
              "Separate verified research (with source URLs), assumptions, and recommendations."),
        "requirements": ("Include an explicit 'cannot tell / retake photo' outcome and an 'outside supported classes' outcome.",
                         "State that results are visual symptom estimates and recommend expert confirmation for decisions."),
        "risk_register": ("Cover: dataset bias and domain shift, misclassification harm, over-confidence, privacy of photos, "
                          "model licence, and user over-reliance.",),
        "ui_spec": ("Include photo guidance before capture, a quality check after capture, confidence display, and a "
                    "clear path when the result is uncertain.",),
        "architecture": ("Show where inference runs, how the model is updated, and what leaves the device.",),
    },
    tasks=(TaskSeed("Define the class list and labelling guide", "Classes, edge cases, 'unknown' policy, labelling rules.", "data"),
           TaskSeed("Collect and label a real-world image set", "Photos from target phones and conditions; double-check labels.", "data", (0,)),
           TaskSeed("Train a baseline by fine-tuning a pretrained model", "Baseline metrics per class on a held-out set.", "ml", (1,)),
           TaskSeed("Calibrate confidence and set the 'uncertain' threshold", "Reliability diagram; threshold chosen from validation data.", "ml", (2,)),
           TaskSeed("Build capture guidance and result screens", "Camera guidance, quality check, result with confidence.", "engineering", (0,)),
           TaskSeed("Integrate the model in the app", "On-device or API inference per the architecture decision.", "engineering", (3, 4)),
           TaskSeed("Field validation with real users and an expert", "Measure real-world accuracy and harmful errors before release.", "research", (5,))),
)

MARKETING = Template(
    id="marketing_campaign",
    label="Marketing campaign",
    description="Audience, message, channels, budget and measurement.",
    topics=(Topic("goal", "What should the campaign change (sign-ups, sales, awareness) and by how much?",
                  "A measurable goal decides channels and budget.", ("requirements", "implementation_plan")),
            Topic("audience", "Who exactly is the audience, and where do they spend attention?",
                  "Audience defines message and channels.", ("requirements", "user_stories")),
            Topic("message", "What single message should they remember, and why should they believe it?",
                  "One clear message beats many weak ones.", ("requirements",)),
            Topic("channels", "Which channels and formats are realistic for your team?", "Channels decide production work.",
                  ("technology_choices", "implementation_plan")),
            _CONSTRAINTS),
    research=(ResearchQuestion("What are competitors saying to this audience, and on which channels?",
                               ("competitor social accounts and ads libraries", "industry reports")),),
    tasks=(TaskSeed("Write the creative brief", "Goal, audience, message, proof, tone.", "marketing"),
           TaskSeed("Produce first assets", "Assets for the top channel.", "design", (0,)),
           TaskSeed("Launch and measure", "Publish, track the goal metric weekly.", "marketing", (1,))),
)

RESEARCH = Template(
    id="research_study",
    label="Research study",
    description="Research question, method, data, ethics and analysis plan.",
    topics=(Topic("question", "What is the precise research question or hypothesis?", "Everything follows from the question.",
                  ("requirements", "feasibility")),
            Topic("method", "Which method fits (survey, experiment, interviews, data analysis), and why?", "Method decides validity.",
                  ("architecture", "implementation_plan")),
            Topic("data", "What data will you collect or reuse, and how will you get consent?", "Data access and ethics gate the study.",
                  ("data_model", "risk_register")),
            _CONSTRAINTS),
    research=(ResearchQuestion("What does existing literature already say about this question?",
                               ("Google Scholar", "arXiv", "systematic reviews")),),
    tasks=(TaskSeed("Literature review", "Summarise prior work and gaps.", "research"),
           TaskSeed("Finalize protocol and ethics", "Method, sampling, consent.", "research", (0,)),
           TaskSeed("Collect data", "Run the protocol.", "research", (1,)),
           TaskSeed("Analyse and write up", "Analysis plan, results, limitations.", "research", (2,))),
)

BUSINESS = Template(
    id="business_plan",
    label="Business idea",
    description="Customer, value proposition, revenue model, costs and go-to-market.",
    topics=(_PROBLEM,
            Topic("customer", "Who pays, and how do they solve this problem today?", "The paying customer may differ from the user.",
                  ("requirements", "feasibility")),
            Topic("revenue", "How will it make money, and roughly at what price?", "The revenue model shapes the product.",
                  ("feasibility", "technology_choices")),
            Topic("costs", "What are the main costs to start and to run it?", "Costs decide viability.", ("feasibility", "risk_register")),
            Topic("go_to_market", "How will the first 10 customers hear about it?", "Distribution is often the hardest part.",
                  ("implementation_plan",)),
            _CONSTRAINTS),
    research=(ResearchQuestion("How large is the target market and who are the competitors?",
                               ("industry reports", "government statistics", "competitor websites")),),
    tasks=(TaskSeed("Talk to 10 potential customers", "Validate problem and willingness to pay.", "research"),
           TaskSeed("Build a simple financial model", "Costs, price, break-even.", "business", (0,)),
           TaskSeed("Run a small go-to-market test", "Landing page or pilot with real prospects.", "marketing", (0,))),
)

TEMPLATES: dict[str, Template] = {t.id: t for t in (GENERAL, SOFTWARE, ML_VISION, MARKETING, RESEARCH, BUSINESS)}

# Keyword hints (English and French) used to pre-select a template. The owner can always change it.
_HINTS: list[tuple[str, str, str]] = [
    ("ml_vision_app", r"\b(photos?|photograph\w*|camera|caméra|images?|pictures?|recogni[sz]\w*|reconnaissance|detect\w*|"
                      r"détect\w*|scan\w*|vision)\b", "it mentions photos, a camera or image recognition"),
    ("marketing_campaign", r"\b(marketing|campaign|campagne|promot\w*|brand\w*|marque|advertis\w*|publicité)\b",
     "it is about promoting something"),
    ("research_study", r"\b(study|survey|research|thesis|étude|enquête|recherche|mémoire)\b", "it is a study or research question"),
    ("business_plan", r"\b(business|startup|company|entreprise|sell|vendre|shop|boutique|revenue|clients?)\b",
     "it is about a business"),
    ("software_product", r"\b(apps?|application|website|site web|software|logiciel|platform|plateforme|tool|outil|saas|api)\b",
     "it describes a software product"),
]


def suggest_template(idea: str) -> tuple[str, str]:
    """Return (template id, reason) from simple keyword hints; 'general' when nothing matches."""
    import re

    text = idea.lower()
    for template_id, pattern, reason in _HINTS:
        if re.search(pattern, text):
            return template_id, reason
    return "general", "no specific kind was recognised"


def get_template(template_id: str) -> Template:
    return TEMPLATES.get(template_id, GENERAL)
