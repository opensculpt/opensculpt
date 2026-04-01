"""Seed patterns for the evolution engine.

Maps agos modules to testable code snippets that pass the sandbox.
Each snippet is a self-contained pattern relevant to its target OS layer.
Used by the evolution cycle to provide known-good patterns when papers
are discovered that match a target module.
"""
from __future__ import annotations

from agos.evolution.code_analyzer import CodePattern

TECHNIQUE_PATTERNS = [
    # L1: Knowledge & Memory
    (["memory", "recall", "retriev", "knowledge base"], "knowledge", "high"),
    (["semantic search", "embedding", "vector stor", "cosine similar"], "knowledge.semantic", "high"),
    (["layer", "hierarch", "priorit", "cascade"], "knowledge.manager", "high"),
    (["consolidat", "compress", "summar", "distill"], "knowledge.consolidator", "medium"),
    (["graph", "entity", "relation", "link predict"], "knowledge.graph", "medium"),
    # L2: Intent & Agent Intelligence
    (["intent", "classif", "understand", "interpret", "nlu"], "intent", "high"),
    (["persona", "role", "system prompt", "instruct"], "intent.personas", "high"),
    (["self-reflect", "critiqu", "self-eval", "introspec"], "intent", "medium"),
    (["proactiv", "suggest", "detect pattern", "anticipat"], "intent.proactive", "medium"),
    # L3: Orchestration & Coordination
    (["multi-agent", "coordinat", "collabor", "team"], "coordination", "high"),
    (["workflow", "orchestrat", "pipeline", "dag"], "orchestration.planner", "high"),
    (["batch", "parallel", "concurrent", "throughput"], "orchestration.planner", "medium"),
    (["schedul", "queue", "priorit", "dispatch"], "orchestration.runtime", "medium"),
    # L4: Policy & Governance
    (["secur", "policy", "permission", "access control"], "policy", "high"),
    (["rate limit", "throttl", "budget", "quota"], "policy", "medium"),
    (["audit", "complian", "governance", "accountab"], "policy.audit", "medium"),
    (["trust", "reliab", "calibrat", "confident"], "policy", "medium"),
    # L5: Events & Observability
    (["event driven", "publish", "subscrib", "message bus"], "events", "medium"),
    (["distributed trac", "observab", "telemetry", "monitor"], "events.tracing", "medium"),
    (["tool use", "function call", "api", "plugin"], "tools", "medium"),
    (["attention", "transformer", "context window"], "kernel", "low"),
    (["cache", "buffer", "working memory", "short-term"], "kernel", "medium"),
    (["self-improv", "meta-learn", "evolv", "adapt"], "evolution", "medium"),
    # L6: Security & Hardening
    (["sandbox", "escap", "bypass", "isolation", "jail"], "policy", "high"),
    (["injection", "prompt inject", "adversarial input"], "policy", "high"),
    (["vulnerab", "exploit", "attack surface", "threat model"], "policy", "high"),
    (["malware", "malicious", "backdoor", "trojan"], "policy", "high"),
    (["authentica", "authoriz", "rbac", "identity"], "policy", "medium"),
    (["encrypt", "hash", "sign", "crypto", "tls"], "policy", "medium"),
    (["code analys", "static analys", "taint", "sast"], "policy.audit", "medium"),
    (["anomaly detect", "intrusion detect", "behavior monitor"], "policy.audit", "medium"),
]

# Testable code snippets that pass the sandbox (mapped to agos modules).
# Each snippet is a self-contained pattern relevant to its target OS layer.
TESTABLE_SNIPPETS = {
    # ── L1: Knowledge & Memory ──────────────────────────────────────
    "knowledge.semantic": CodePattern(
        name="Softmax Diversity Scorer",
        description="Temperature-controlled probabilistic scoring",
        source_file="evolved.py", source_repo="arxiv", agos_module="knowledge.semantic", priority="high",
        code_snippet=(
            "import math\nimport random\n\n"
            "def softmax_score(values, temperature=0.3):\n"
            "    if not values: return []\n"
            "    exp_vals = [math.exp(v / max(temperature, 0.01)) for v in values]\n"
            "    total = sum(exp_vals)\n"
            "    return [v / total for v in exp_vals]\n\n"
            "scores = softmax_score([0.9, 0.7, 0.4, 0.2, 0.1])\n"
            "assert abs(sum(scores) - 1.0) < 0.001\n"
            "print(f'Softmax scores: {[round(s,3) for s in scores]}')\n"
            "print('PASS: Softmax diversity scorer validated')\n"
        ),
    ),
    "knowledge": CodePattern(
        name="Adaptive Confidence Tracker",
        description="Access-frequency-based confidence with decay",
        source_file="evolved.py", source_repo="arxiv", agos_module="knowledge", priority="high",
        code_snippet=(
            "import math\n\n"
            "class ConfidenceTracker:\n"
            "    def __init__(self, decay=0.95):\n"
            "        self.decay = decay\n"
            "        self.counts = {}\n"
            "        self.conf = {}\n"
            "    def access(self, key):\n"
            "        self.counts[key] = self.counts.get(key, 0) + 1\n"
            "        self.conf[key] = 0.5 + 0.5 * (1 - math.exp(-self.counts[key] / 5))\n"
            "        return self.conf[key]\n"
            "    def decay_all(self):\n"
            "        for k in self.conf: self.conf[k] *= self.decay\n\n"
            "t = ConfidenceTracker()\n"
            "for _ in range(10): c = t.access('k1')\n"
            "assert c > 0.85\n"
            "t.decay_all()\n"
            "print(f'After 10 accesses: {c:.4f}, after decay: {t.conf[\"k1\"]:.4f}')\n"
            "print('PASS: Adaptive confidence tracker validated')\n"
        ),
    ),
    "knowledge.manager": CodePattern(
        name="Layered Memory Retriever",
        description="Priority-ordered memory layers",
        source_file="evolved.py", source_repo="arxiv", agos_module="knowledge.manager", priority="high",
        code_snippet=(
            "class Layer:\n"
            "    def __init__(self, name, pri, data):\n"
            "        self.name, self.pri, self.data = name, pri, data\n"
            "    def query(self, q, n=5):\n"
            "        return [d for d in self.data if q.lower() in d.lower()][:n]\n\n"
            "def layered_recall(layers, q, limit=10):\n"
            "    out = []\n"
            "    for l in sorted(layers, key=lambda x: x.pri, reverse=True):\n"
            "        if len(out) >= limit: break\n"
            "        out.extend(l.query(q, limit - len(out)))\n"
            "    return out\n\n"
            "w = Layer('working', 100, ['agent running now', 'current goal: evolve'])\n"
            "e = Layer('episodic', 50, ['agent completed scan', 'agent learned'])\n"
            "s = Layer('semantic', 10, ['agents use events', 'agent memory works'])\n"
            "r = layered_recall([s, w, e], 'agent', 3)\n"
            "assert len(r) == 3 and 'now' in r[0]\n"
            "print(f'Layered recall: {r}')\n"
            "print('PASS: Layered retriever validated')\n"
        ),
    ),
    "knowledge.graph": CodePattern(
        name="Weighted Graph Traverser",
        description="BFS traversal with edge-weight decay over hops",
        source_file="evolved.py", source_repo="arxiv", agos_module="knowledge.graph", priority="medium",
        code_snippet=(
            "from collections import defaultdict\n\n"
            "class WeightedGraph:\n"
            "    def __init__(self):\n"
            "        self.edges = defaultdict(list)\n"
            "    def add(self, src, rel, dst, weight=1.0):\n"
            "        self.edges[src].append((dst, rel, weight))\n"
            "    def traverse(self, start, max_depth=3, decay=0.7):\n"
            "        visited = {}\n"
            "        queue = [(start, 1.0, 0)]\n"
            "        while queue:\n"
            "            node, score, depth = queue.pop(0)\n"
            "            if node in visited or depth > max_depth:\n"
            "                continue\n"
            "            visited[node] = round(score, 4)\n"
            "            for dst, rel, w in self.edges.get(node, []):\n"
            "                queue.append((dst, score * w * decay, depth + 1))\n"
            "        return visited\n\n"
            "g = WeightedGraph()\n"
            "g.add('agent', 'uses', 'memory', 0.9)\n"
            "g.add('memory', 'contains', 'facts', 0.8)\n"
            "g.add('agent', 'has', 'policy', 0.7)\n"
            "g.add('facts', 'derived_from', 'papers', 0.6)\n"
            "result = g.traverse('agent', max_depth=3)\n"
            "assert 'memory' in result and 'facts' in result\n"
            "assert result['agent'] == 1.0\n"
            "assert result['memory'] < 1.0\n"
            "print(f'Graph traversal: {result}')\n"
            "print('PASS: Weighted graph traverser validated')\n"
        ),
    ),
    "knowledge.consolidator": CodePattern(
        name="Cluster Consolidator",
        description="Groups similar items and produces summaries",
        source_file="evolved.py", source_repo="arxiv", agos_module="knowledge.consolidator", priority="medium",
        code_snippet=(
            "from collections import defaultdict\n\n"
            "def similarity(a, b):\n"
            "    wa, wb = set(a.lower().split()), set(b.lower().split())\n"
            "    if not wa or not wb: return 0.0\n"
            "    return len(wa & wb) / len(wa | wb)\n\n"
            "def cluster(items, threshold=0.3):\n"
            "    clusters = []\n"
            "    for item in items:\n"
            "        placed = False\n"
            "        for cluster in clusters:\n"
            "            if similarity(item, cluster[0]) >= threshold:\n"
            "                cluster.append(item)\n"
            "                placed = True\n"
            "                break\n"
            "        if not placed:\n"
            "            clusters.append([item])\n"
            "    return clusters\n\n"
            "def consolidate(clusters):\n"
            "    return [f'[{len(c)} items] {c[0][:40]}...' for c in clusters if len(c) >= 2]\n\n"
            "items = ['agent scanned files', 'agent scanned directories',\n"
            "         'policy check passed', 'policy check approved',\n"
            "         'memory stored fact', 'evolution found paper']\n"
            "cl = cluster(items)\n"
            "summaries = consolidate(cl)\n"
            "assert len(cl) >= 3\n"
            "print(f'Clusters: {len(cl)}, Summaries: {summaries}')\n"
            "print('PASS: Cluster consolidator validated')\n"
        ),
    ),
    # ── L2: Intent & Agent Intelligence ─────────────────────────────
    "intent": CodePattern(
        name="Intent Classifier",
        description="Keyword-scored intent classification with confidence",
        source_file="evolved.py", source_repo="arxiv", agos_module="intent", priority="high",
        code_snippet=(
            "import math\n\n"
            "INTENT_RULES = {\n"
            "    'research': ['search', 'find', 'look up', 'investigate', 'analyze'],\n"
            "    'code': ['write', 'implement', 'fix', 'refactor', 'build'],\n"
            "    'review': ['review', 'check', 'audit', 'inspect', 'validate'],\n"
            "    'monitor': ['watch', 'track', 'alert', 'detect', 'observe'],\n"
            "    'automate': ['schedule', 'trigger', 'automate', 'repeat', 'cron'],\n"
            "}\n\n"
            "def classify_intent(text):\n"
            "    text_lower = text.lower()\n"
            "    scores = {}\n"
            "    for intent, keywords in INTENT_RULES.items():\n"
            "        score = sum(1 for kw in keywords if kw in text_lower)\n"
            "        if score > 0:\n"
            "            scores[intent] = score\n"
            "    if not scores:\n"
            "        return 'unknown', 0.0\n"
            "    best = max(scores, key=scores.get)\n"
            "    conf = scores[best] / max(len(INTENT_RULES[best]), 1)\n"
            "    return best, round(conf, 3)\n\n"
            "tests = [\n"
            "    ('search for recent papers on memory', 'research'),\n"
            "    ('write a function to sort items', 'code'),\n"
            "    ('review the security audit logs', 'review'),\n"
            "    ('watch for anomalies and alert', 'monitor'),\n"
            "]\n"
            "for text, expected in tests:\n"
            "    intent, conf = classify_intent(text)\n"
            "    assert intent == expected, f'{text!r}: got {intent}, expected {expected}'\n"
            "print(f'Classified {len(tests)} intents correctly')\n"
            "print('PASS: Intent classifier validated')\n"
        ),
    ),
    "intent.personas": CodePattern(
        name="Persona Capability Matcher",
        description="Role-based agent selection with capability scoring",
        source_file="evolved.py", source_repo="arxiv", agos_module="intent.personas", priority="high",
        code_snippet=(
            "class Persona:\n"
            "    def __init__(self, name, capabilities, budget, max_turns):\n"
            "        self.name = name\n"
            "        self.capabilities = set(capabilities)\n"
            "        self.budget = budget\n"
            "        self.max_turns = max_turns\n\n"
            "def match_persona(task_needs, personas):\n"
            "    scored = []\n"
            "    for p in personas:\n"
            "        overlap = len(task_needs & p.capabilities)\n"
            "        coverage = overlap / max(len(task_needs), 1)\n"
            "        efficiency = overlap / max(len(p.capabilities), 1)\n"
            "        score = 0.6 * coverage + 0.4 * efficiency\n"
            "        scored.append((p, round(score, 3)))\n"
            "    scored.sort(key=lambda x: -x[1])\n"
            "    return scored\n\n"
            "personas = [\n"
            "    Persona('researcher', {'search', 'read', 'analyze', 'http'}, 200000, 30),\n"
            "    Persona('coder', {'write', 'shell', 'python', 'test'}, 200000, 40),\n"
            "    Persona('reviewer', {'read', 'analyze', 'audit'}, 100000, 20),\n"
            "    Persona('orchestrator', {'search', 'read', 'write', 'shell', 'http', 'python'}, 300000, 50),\n"
            "]\n"
            "need = {'read', 'analyze', 'audit'}\n"
            "ranked = match_persona(need, personas)\n"
            "assert ranked[0][0].name == 'reviewer'\n"
            "print(f'Best match for {need}: {ranked[0][0].name} (score={ranked[0][1]})')\n"
            "print('PASS: Persona capability matcher validated')\n"
        ),
    ),
    "intent.proactive": CodePattern(
        name="Anomaly Pattern Detector",
        description="Detects anomalous patterns in event frequency streams",
        source_file="evolved.py", source_repo="arxiv", agos_module="intent.proactive", priority="medium",
        code_snippet=(
            "import math\n\n"
            "class AnomalyDetector:\n"
            "    def __init__(self, window=10, threshold=2.0):\n"
            "        self.window = window\n"
            "        self.threshold = threshold\n"
            "        self.history = []\n"
            "    def observe(self, value):\n"
            "        self.history.append(value)\n"
            "        if len(self.history) < self.window:\n"
            "            return False, 0.0\n"
            "        recent = self.history[-self.window:]\n"
            "        mean = sum(recent) / len(recent)\n"
            "        var = sum((x - mean) ** 2 for x in recent) / len(recent)\n"
            "        std = math.sqrt(var) if var > 0 else 0.001\n"
            "        z_score = abs(value - mean) / std\n"
            "        return z_score > self.threshold, round(z_score, 3)\n\n"
            "d = AnomalyDetector(window=5, threshold=2.0)\n"
            "normal = [10, 12, 11, 13, 10, 11, 12, 10]\n"
            "for v in normal:\n"
            "    is_anom, z = d.observe(v)\n"
            "    assert not is_anom, f'False positive on {v}'\n"
            "is_anom, z = d.observe(50)\n"
            "assert is_anom, 'Failed to detect spike'\n"
            "print(f'Anomaly detected: z_score={z}')\n"
            "print('PASS: Anomaly pattern detector validated')\n"
        ),
    ),
    # ── L3: Orchestration & Coordination ────────────────────────────
    "coordination": CodePattern(
        name="Semaphore Batch Processor",
        description="Concurrent operations with parallelism limit",
        source_file="evolved.py", source_repo="arxiv", agos_module="coordination", priority="medium",
        code_snippet=(
            "import asyncio\n\n"
            "async def batch(items, fn, limit=3):\n"
            "    sem = asyncio.Semaphore(limit)\n"
            "    out = []\n"
            "    async def run(x):\n"
            "        async with sem:\n"
            "            out.append(await fn(x))\n"
            "    await asyncio.gather(*(run(i) for i in items))\n"
            "    return sorted(out)\n\n"
            "async def dbl(x):\n"
            "    await asyncio.sleep(0.01)\n"
            "    return x * 2\n\n"
            "async def main():\n"
            "    r = await batch([1,2,3,4,5], dbl, 2)\n"
            "    assert r == [2,4,6,8,10]\n"
            "    print(f'Batch: {r}')\n"
            "    print('PASS: Semaphore batch validated')\n\n"
            "asyncio.run(main())\n"
        ),
    ),
    "orchestration.planner": CodePattern(
        name="DAG Task Planner",
        description="Dependency-aware task planner with topological ordering",
        source_file="evolved.py", source_repo="arxiv", agos_module="orchestration.planner", priority="high",
        code_snippet=(
            "from collections import defaultdict\n\n"
            "class TaskDAG:\n"
            "    def __init__(self):\n"
            "        self.tasks = {}\n"
            "        self.deps = defaultdict(set)\n"
            "    def add(self, name, deps=None):\n"
            "        self.tasks[name] = {'status': 'pending'}\n"
            "        if deps:\n"
            "            for d in deps:\n"
            "                self.deps[name].add(d)\n"
            "    def topo_sort(self):\n"
            "        in_deg = defaultdict(int)\n"
            "        for t in self.tasks: in_deg[t] = 0\n"
            "        for t, ds in self.deps.items():\n"
            "            in_deg[t] = len(ds)\n"
            "        queue = [t for t in self.tasks if in_deg[t] == 0]\n"
            "        order = []\n"
            "        while queue:\n"
            "            t = queue.pop(0)\n"
            "            order.append(t)\n"
            "            for dep_t, dep_set in self.deps.items():\n"
            "                if t in dep_set:\n"
            "                    in_deg[dep_t] -= 1\n"
            "                    if in_deg[dep_t] == 0:\n"
            "                        queue.append(dep_t)\n"
            "        return order\n"
            "    def parallel_groups(self):\n"
            "        order = self.topo_sort()\n"
            "        groups, done = [], set()\n"
            "        while order:\n"
            "            batch = [t for t in order if self.deps[t] <= done]\n"
            "            groups.append(batch)\n"
            "            done.update(batch)\n"
            "            order = [t for t in order if t not in done]\n"
            "        return groups\n\n"
            "dag = TaskDAG()\n"
            "dag.add('fetch_data')\n"
            "dag.add('parse', deps=['fetch_data'])\n"
            "dag.add('validate', deps=['fetch_data'])\n"
            "dag.add('transform', deps=['parse', 'validate'])\n"
            "dag.add('store', deps=['transform'])\n"
            "order = dag.topo_sort()\n"
            "groups = dag.parallel_groups()\n"
            "assert order[0] == 'fetch_data'\n"
            "assert order[-1] == 'store'\n"
            "assert len(groups) == 4\n"
            "assert set(groups[1]) == {'parse', 'validate'}\n"
            "print(f'Execution order: {order}')\n"
            "print(f'Parallel groups: {groups}')\n"
            "print('PASS: DAG task planner validated')\n"
        ),
    ),
    "orchestration.runtime": CodePattern(
        name="Priority Fair Scheduler",
        description="Priority queue scheduler with fair time-slicing",
        source_file="evolved.py", source_repo="arxiv", agos_module="orchestration.runtime", priority="medium",
        code_snippet=(
            "import collections\n\n"
            "class PriorityScheduler:\n"
            "    def __init__(self, max_concurrent=3):\n"
            "        self.max_concurrent = max_concurrent\n"
            "        self.queues = collections.defaultdict(list)\n"
            "        self.running = []\n"
            "        self.completed = []\n"
            "    def submit(self, task_id, priority=5):\n"
            "        self.queues[priority].append(task_id)\n"
            "    def schedule(self):\n"
            "        batch = []\n"
            "        for pri in sorted(self.queues.keys(), reverse=True):\n"
            "            while self.queues[pri] and len(batch) < self.max_concurrent:\n"
            "                batch.append((pri, self.queues[pri].pop(0)))\n"
            "        self.running = batch\n"
            "        return batch\n"
            "    def complete(self):\n"
            "        self.completed.extend(self.running)\n"
            "        self.running = []\n\n"
            "s = PriorityScheduler(max_concurrent=2)\n"
            "s.submit('low_task', priority=1)\n"
            "s.submit('critical', priority=10)\n"
            "s.submit('normal', priority=5)\n"
            "s.submit('urgent', priority=8)\n"
            "b1 = s.schedule()\n"
            "assert b1[0][1] == 'critical'\n"
            "s.complete()\n"
            "b2 = s.schedule()\n"
            "assert len(b2) == 2\n"
            "print(f'Batch 1: {b1}')\n"
            "print(f'Batch 2: {b2}')\n"
            "print('PASS: Priority fair scheduler validated')\n"
        ),
    ),
    # ── L4: Policy & Governance ─────────────────────────────────────
    "policy": CodePattern(
        name="Policy Rule Engine",
        description="Allow/deny rule chains with wildcard matching",
        source_file="evolved.py", source_repo="arxiv", agos_module="policy", priority="high",
        code_snippet=(
            "import re\n\n"
            "class PolicyRule:\n"
            "    def __init__(self, pattern, action, effect):\n"
            "        self.pattern = pattern\n"
            "        self.action = action\n"
            "        self.effect = effect\n"
            "    def matches(self, agent, action):\n"
            "        p = self.pattern.replace('*', '.*')\n"
            "        return bool(re.match(p, agent)) and (\n"
            "            self.action == '*' or self.action == action\n"
            "        )\n\n"
            "class PolicyEngine:\n"
            "    def __init__(self):\n"
            "        self.rules = []\n"
            "    def add_rule(self, pattern, action, effect):\n"
            "        self.rules.append(PolicyRule(pattern, action, effect))\n"
            "    def check(self, agent, action):\n"
            "        for rule in self.rules:\n"
            "            if rule.matches(agent, action):\n"
            "                return rule.effect\n"
            "        return 'deny'\n\n"
            "pe = PolicyEngine()\n"
            "pe.add_rule('admin*', '*', 'allow')\n"
            "pe.add_rule('agent_*', 'read', 'allow')\n"
            "pe.add_rule('agent_*', 'write', 'deny')\n"
            "pe.add_rule('*', '*', 'deny')\n"
            "assert pe.check('admin_root', 'delete') == 'allow'\n"
            "assert pe.check('agent_scanner', 'read') == 'allow'\n"
            "assert pe.check('agent_scanner', 'write') == 'deny'\n"
            "assert pe.check('unknown', 'read') == 'deny'\n"
            "print('Policy checks: 4/4 passed')\n"
            "print('PASS: Policy rule engine validated')\n"
        ),
    ),
    "policy.audit": CodePattern(
        name="Hash Chain Audit Log",
        description="Tamper-evident audit trail with chained hashes",
        source_file="evolved.py", source_repo="arxiv", agos_module="policy.audit", priority="medium",
        code_snippet=(
            "import hashlib\nimport json\n\n"
            "class AuditLog:\n"
            "    def __init__(self):\n"
            "        self.entries = []\n"
            "        self.prev_hash = '0' * 64\n"
            "    def record(self, agent, action, detail):\n"
            "        entry = {'agent': agent, 'action': action, 'detail': detail,\n"
            "                 'prev_hash': self.prev_hash}\n"
            "        payload = json.dumps(entry, sort_keys=True)\n"
            "        entry['hash'] = hashlib.sha256(payload.encode()).hexdigest()\n"
            "        self.entries.append(entry)\n"
            "        self.prev_hash = entry['hash']\n"
            "    def verify(self):\n"
            "        prev = '0' * 64\n"
            "        for e in self.entries:\n"
            "            if e['prev_hash'] != prev:\n"
            "                return False\n"
            "            check = {k: v for k, v in e.items() if k != 'hash'}\n"
            "            expected = hashlib.sha256(json.dumps(check, sort_keys=True).encode()).hexdigest()\n"
            "            if e['hash'] != expected:\n"
            "                return False\n"
            "            prev = e['hash']\n"
            "        return True\n\n"
            "log = AuditLog()\n"
            "log.record('scanner', 'scan', 'scanned /app')\n"
            "log.record('evolver', 'evolve', 'applied softmax')\n"
            "log.record('policy', 'check', 'agent_1 denied write')\n"
            "assert log.verify()\n"
            "assert len(log.entries) == 3\n"
            "assert log.entries[0]['prev_hash'] == '0' * 64\n"
            "print(f'Audit log: {len(log.entries)} entries, chain valid')\n"
            "print('PASS: Hash chain audit log validated')\n"
        ),
    ),
    # ── L5: Events & Observability ──────────────────────────────────
    "events": CodePattern(
        name="Wildcard Event Bus",
        description="Pub/sub with wildcard topic matching and priority dispatch",
        source_file="evolved.py", source_repo="arxiv", agos_module="events", priority="medium",
        code_snippet=(
            "import re\n\n"
            "class MicroBus:\n"
            "    def __init__(self):\n"
            "        self.subs = []\n"
            "        self.log = []\n"
            "    def subscribe(self, pattern, handler, priority=0):\n"
            "        regex = pattern.replace('.', r'\\.').replace('*', '.*')\n"
            "        self.subs.append((regex, handler, priority))\n"
            "        self.subs.sort(key=lambda x: -x[2])\n"
            "    def emit(self, topic, data=None):\n"
            "        self.log.append(topic)\n"
            "        matched = 0\n"
            "        for regex, handler, _ in self.subs:\n"
            "            if re.match(regex, topic):\n"
            "                handler(topic, data or {})\n"
            "                matched += 1\n"
            "        return matched\n\n"
            "results = []\n"
            "bus = MicroBus()\n"
            "bus.subscribe('agent.*', lambda t, d: results.append(('agent', t)))\n"
            "bus.subscribe('system.*', lambda t, d: results.append(('system', t)))\n"
            "bus.subscribe('*', lambda t, d: results.append(('all', t)), priority=-1)\n"
            "bus.emit('agent.spawned', {'id': '123'})\n"
            "bus.emit('system.boot', {'phase': 'kernel'})\n"
            "bus.emit('evolution.cycle', {})\n"
            "assert len(results) == 5\n"
            "assert results[0] == ('agent', 'agent.spawned')\n"
            "print(f'Events dispatched: {len(bus.log)}, handlers invoked: {len(results)}')\n"
            "print('PASS: Wildcard event bus validated')\n"
        ),
    ),
    "events.tracing": CodePattern(
        name="Span Tree Tracer",
        description="Distributed trace builder with nested spans",
        source_file="evolved.py", source_repo="arxiv", agos_module="events.tracing", priority="medium",
        code_snippet=(
            "import time\n\n"
            "class Span:\n"
            "    def __init__(self, name, parent=None):\n"
            "        self.name = name\n"
            "        self.parent = parent\n"
            "        self.children = []\n"
            "        self.start = time.monotonic()\n"
            "        self.end = None\n"
            "        self.metadata = {}\n"
            "    def finish(self):\n"
            "        self.end = time.monotonic()\n"
            "    @property\n"
            "    def duration_ms(self):\n"
            "        if self.end is None: return 0\n"
            "        return round((self.end - self.start) * 1000, 2)\n"
            "    def child(self, name):\n"
            "        c = Span(name, parent=self)\n"
            "        self.children.append(c)\n"
            "        return c\n"
            "    def tree(self, depth=0):\n"
            "        lines = [f\"{'  ' * depth}{self.name} ({self.duration_ms}ms)\"]\n"
            "        for c in self.children:\n"
            "            lines.extend(c.tree(depth + 1))\n"
            "        return lines\n\n"
            "root = Span('request')\n"
            "parse = root.child('parse_intent')\n"
            "parse.finish()\n"
            "plan = root.child('plan_execution')\n"
            "agent1 = plan.child('agent_researcher')\n"
            "agent1.finish()\n"
            "agent2 = plan.child('agent_coder')\n"
            "agent2.finish()\n"
            "plan.finish()\n"
            "root.finish()\n"
            "tree = root.tree()\n"
            "assert len(tree) == 5\n"
            "assert 'request' in tree[0]\n"
            "assert 'agent_researcher' in tree[3]\n"
            "print('\\n'.join(tree))\n"
            "print('PASS: Span tree tracer validated')\n"
        ),
    ),
    # ── Cross-cutting: Tools, Kernel, Evolution ─────────────────────
    "tools": CodePattern(
        name="Tool Capability Registry",
        description="Tool discovery with capability matching and scoring",
        source_file="evolved.py", source_repo="arxiv", agos_module="tools", priority="medium",
        code_snippet=(
            "class Tool:\n"
            "    def __init__(self, name, capabilities, cost=1):\n"
            "        self.name = name\n"
            "        self.capabilities = set(capabilities)\n"
            "        self.cost = cost\n"
            "        self.uses = 0\n\n"
            "class ToolRegistry:\n"
            "    def __init__(self):\n"
            "        self.tools = []\n"
            "    def register(self, tool):\n"
            "        self.tools.append(tool)\n"
            "    def find(self, needs, max_cost=10):\n"
            "        candidates = []\n"
            "        for t in self.tools:\n"
            "            if t.cost > max_cost:\n"
            "                continue\n"
            "            overlap = len(needs & t.capabilities)\n"
            "            if overlap > 0:\n"
            "                score = overlap / len(needs) - t.cost * 0.01\n"
            "                candidates.append((t, round(score, 3)))\n"
            "        candidates.sort(key=lambda x: -x[1])\n"
            "        return candidates\n\n"
            "reg = ToolRegistry()\n"
            "reg.register(Tool('shell', {'execute', 'script', 'process'}, cost=3))\n"
            "reg.register(Tool('http', {'fetch', 'api', 'download'}, cost=2))\n"
            "reg.register(Tool('file_read', {'read', 'search', 'analyze'}, cost=1))\n"
            "reg.register(Tool('python', {'execute', 'compute', 'analyze'}, cost=2))\n"
            "results = reg.find({'read', 'analyze'}, max_cost=5)\n"
            "assert results[0][0].name == 'file_read'\n"
            "assert len(results) >= 2\n"
            "print(f'Best tool for read+analyze: {results[0][0].name} (score={results[0][1]})')\n"
            "print('PASS: Tool capability registry validated')\n"
        ),
    ),
    "kernel": CodePattern(
        name="TTL LRU Cache",
        description="LRU cache with TTL expiration and hit-rate tracking",
        source_file="evolved.py", source_repo="arxiv", agos_module="kernel", priority="medium",
        code_snippet=(
            "import time\nfrom collections import OrderedDict\n\n"
            "class TTLCache:\n"
            "    def __init__(self, maxsize=100, ttl=60):\n"
            "        self.maxsize = maxsize\n"
            "        self.ttl = ttl\n"
            "        self.cache = OrderedDict()\n"
            "        self.hits = 0\n"
            "        self.misses = 0\n"
            "    def get(self, key):\n"
            "        if key in self.cache:\n"
            "            val, ts = self.cache[key]\n"
            "            if time.monotonic() - ts < self.ttl:\n"
            "                self.cache.move_to_end(key)\n"
            "                self.hits += 1\n"
            "                return val\n"
            "            del self.cache[key]\n"
            "        self.misses += 1\n"
            "        return None\n"
            "    def put(self, key, value):\n"
            "        self.cache[key] = (value, time.monotonic())\n"
            "        self.cache.move_to_end(key)\n"
            "        if len(self.cache) > self.maxsize:\n"
            "            self.cache.popitem(last=False)\n"
            "    @property\n"
            "    def hit_rate(self):\n"
            "        total = self.hits + self.misses\n"
            "        return round(self.hits / total, 3) if total else 0.0\n\n"
            "c = TTLCache(maxsize=3, ttl=10)\n"
            "c.put('a', 1); c.put('b', 2); c.put('c', 3)\n"
            "assert c.get('a') == 1\n"
            "c.put('d', 4)\n"
            "assert c.get('b') is None\n"
            "assert c.get('a') == 1\n"
            "assert c.hit_rate > 0.4\n"
            "print(f'Cache: hits={c.hits}, misses={c.misses}, rate={c.hit_rate}')\n"
            "print('PASS: TTL LRU cache validated')\n"
        ),
    ),
    "evolution": CodePattern(
        name="Fitness Proportionate Selector",
        description="Roulette wheel selection for evolutionary strategies",
        source_file="evolved.py", source_repo="arxiv", agos_module="evolution", priority="medium",
        code_snippet=(
            "import random\n\n"
            "class Strategy:\n"
            "    def __init__(self, name, fitness):\n"
            "        self.name = name\n"
            "        self.fitness = fitness\n"
            "        self.selected_count = 0\n\n"
            "def roulette_select(strategies, n=1):\n"
            "    total = sum(s.fitness for s in strategies)\n"
            "    if total == 0:\n"
            "        return random.sample(strategies, min(n, len(strategies)))\n"
            "    selected = []\n"
            "    for _ in range(n):\n"
            "        pick = random.uniform(0, total)\n"
            "        current = 0\n"
            "        for s in strategies:\n"
            "            current += s.fitness\n"
            "            if current >= pick:\n"
            "                s.selected_count += 1\n"
            "                selected.append(s)\n"
            "                break\n"
            "    return selected\n\n"
            "random.seed(42)\n"
            "strats = [\n"
            "    Strategy('softmax', 0.9),\n"
            "    Strategy('layered', 0.7),\n"
            "    Strategy('confidence', 0.3),\n"
            "    Strategy('weak', 0.1),\n"
            "]\n"
            "counts = {s.name: 0 for s in strats}\n"
            "for _ in range(1000):\n"
            "    picked = roulette_select(strats, 1)\n"
            "    counts[picked[0].name] += 1\n"
            "assert counts['softmax'] > counts['weak']\n"
            "assert counts['softmax'] > 300\n"
            "print(f'Selection distribution: {counts}')\n"
            "print('PASS: Fitness proportionate selector validated')\n"
        ),
    ),
    # ── L6: Security & Hardening ────────────────────────────────────
    "policy.security": CodePattern(
        name="AST Sandbox Validator",
        description="Static analysis to detect sandbox escape patterns in Python code",
        source_file="evolved.py", source_repo="security", agos_module="policy.security", priority="high",
        code_snippet=(
            "import ast\nimport re\n\n"
            "DANGEROUS_DUNDERS = {\n"
            "    '__subclasses__', '__mro__', '__globals__',\n"
            "    '__code__', '__func__', '__builtins__',\n"
            "    '__import__', '__loader__', '__spec__',\n"
            "}\n\n"
            "DANGEROUS_CALLS = {'exec', 'eval', 'compile', '__import__',\n"
            "                   'getattr', 'setattr', 'delattr', 'globals',\n"
            "                   'locals', 'vars', 'open', 'breakpoint'}\n\n"
            "def validate_code_safety(code: str) -> list[str]:\n"
            "    issues = []\n"
            "    try:\n"
            "        tree = ast.parse(code)\n"
            "    except SyntaxError as e:\n"
            "        return [f'Syntax error: {e}']\n"
            "    for node in ast.walk(tree):\n"
            "        if isinstance(node, ast.Attribute):\n"
            "            if node.attr in DANGEROUS_DUNDERS:\n"
            "                issues.append(f'Dangerous dunder: .{node.attr}')\n"
            "        elif isinstance(node, ast.Call):\n"
            "            if isinstance(node.func, ast.Name):\n"
            "                if node.func.id in DANGEROUS_CALLS:\n"
            "                    issues.append(f'Dangerous call: {node.func.id}()')\n"
            "    return issues\n\n"
            "# Tests\n"
            "assert validate_code_safety('x = 1 + 2') == []\n"
            "assert len(validate_code_safety('eval(\"bad\")')) == 1\n"
            "assert len(validate_code_safety('x.__subclasses__()')) == 1\n"
            "assert len(validate_code_safety('getattr(obj, \"x\")')) == 1\n"
            "safe_code = 'def add(a, b):\\n    return a + b\\nresult = add(1, 2)'\n"
            "assert validate_code_safety(safe_code) == []\n"
            "print('PASS: AST sandbox validator working')\n"
        ),
    ),
}

# Alternate snippets for modules that need more variety.
# On each cycle, a different snippet is selected to avoid duplicate evolved files.
_ALTERNATE_SNIPPETS: dict[str, list[CodePattern]] = {
    "knowledge.semantic": [
        CodePattern(
            name="Cosine Similarity Ranker",
            description="TF-IDF-style cosine similarity for document ranking",
            source_file="evolved.py", source_repo="arxiv", agos_module="knowledge.semantic", priority="high",
            code_snippet=(
                "import math\nfrom collections import Counter\n\n"
                "def tfidf_vector(text, vocab):\n"
                "    words = text.lower().split()\n"
                "    tf = Counter(words)\n"
                "    return [tf.get(w, 0) / max(len(words), 1) for w in vocab]\n\n"
                "def cosine_sim(a, b):\n"
                "    dot = sum(x * y for x, y in zip(a, b))\n"
                "    na = math.sqrt(sum(x*x for x in a))\n"
                "    nb = math.sqrt(sum(x*x for x in b))\n"
                "    return dot / (na * nb) if na and nb else 0.0\n\n"
                "docs = ['agent memory retrieval', 'semantic search vectors',\n"
                "        'policy engine rules', 'agent memory search']\n"
                "vocab = sorted(set(' '.join(docs).lower().split()))\n"
                "query_vec = tfidf_vector('agent memory', vocab)\n"
                "scores = [(i, round(cosine_sim(query_vec, tfidf_vector(d, vocab)), 3)) for i, d in enumerate(docs)]\n"
                "scores.sort(key=lambda x: -x[1])\n"
                "assert scores[0][1] > scores[-1][1]\n"
                "print(f'Rankings: {scores}')\n"
                "print('PASS: Cosine similarity ranker validated')\n"
            ),
        ),
    ],
    "knowledge": [
        CodePattern(
            name="Exponential Moving Average Tracker",
            description="EMA-based signal smoothing for knowledge confidence",
            source_file="evolved.py", source_repo="arxiv", agos_module="knowledge", priority="high",
            code_snippet=(
                "class EMATracker:\n"
                "    def __init__(self, alpha=0.3):\n"
                "        self.alpha = alpha\n"
                "        self.values = {}\n"
                "    def update(self, key, value):\n"
                "        if key not in self.values:\n"
                "            self.values[key] = value\n"
                "        else:\n"
                "            self.values[key] = self.alpha * value + (1 - self.alpha) * self.values[key]\n"
                "        return round(self.values[key], 4)\n\n"
                "t = EMATracker(alpha=0.3)\n"
                "results = []\n"
                "for v in [1.0, 0.8, 0.9, 0.7, 0.85, 0.95]:\n"
                "    results.append(t.update('sig', v))\n"
                "assert abs(results[-1] - 0.85) < 0.15\n"
                "assert results[0] == 1.0\n"
                "print(f'EMA series: {results}')\n"
                "print('PASS: Exponential moving average tracker validated')\n"
            ),
        ),
    ],
    "policy": [
        CodePattern(
            name="Token Budget Enforcer",
            description="Dynamic token budget with burst allowance and decay",
            source_file="evolved.py", source_repo="arxiv", agos_module="policy", priority="high",
            code_snippet=(
                "class TokenBudget:\n"
                "    def __init__(self, limit, burst_factor=1.5):\n"
                "        self.limit = limit\n"
                "        self.burst_limit = int(limit * burst_factor)\n"
                "        self.used = 0\n"
                "        self.violations = 0\n"
                "    def request(self, tokens):\n"
                "        if self.used + tokens > self.burst_limit:\n"
                "            self.violations += 1\n"
                "            return False\n"
                "        self.used += tokens\n"
                "        return True\n"
                "    def decay(self, factor=0.8):\n"
                "        self.used = int(self.used * factor)\n"
                "    @property\n"
                "    def utilization(self):\n"
                "        return round(self.used / self.limit, 3) if self.limit else 0\n\n"
                "b = TokenBudget(limit=1000, burst_factor=1.5)\n"
                "assert b.request(500)\n"
                "assert b.request(400)\n"
                "assert b.utilization == 0.9\n"
                "assert not b.request(700)\n"
                "assert b.violations == 1\n"
                "b.decay(0.5)\n"
                "assert b.request(700)\n"
                "print(f'Budget: used={b.used}, violations={b.violations}, util={b.utilization}')\n"
                "print('PASS: Token budget enforcer validated')\n"
            ),
        ),
    ],
    "orchestration.planner": [
        CodePattern(
            name="Strategy Selector",
            description="Empirical strategy selection based on task characteristics",
            source_file="evolved.py", source_repo="arxiv", agos_module="orchestration.planner", priority="high",
            code_snippet=(
                "class StrategyRecord:\n"
                "    def __init__(self, name):\n"
                "        self.name = name\n"
                "        self.successes = 0\n"
                "        self.failures = 0\n"
                "        self.total_tokens = 0\n"
                "    @property\n"
                "    def score(self):\n"
                "        total = self.successes + self.failures\n"
                "        if total == 0: return 0.5\n"
                "        success_rate = self.successes / total\n"
                "        efficiency = 1.0 / (1 + self.total_tokens / max(total, 1) / 10000)\n"
                "        return round(0.7 * success_rate + 0.3 * efficiency, 3)\n\n"
                "def select_strategy(records, task_size):\n"
                "    if task_size == 'small':\n"
                "        candidates = [r for r in records if r.name in ('solo', 'pipeline')]\n"
                "    else:\n"
                "        candidates = [r for r in records if r.name in ('parallel', 'debate')]\n"
                "    if not candidates: candidates = records\n"
                "    return max(candidates, key=lambda r: r.score)\n\n"
                "solo = StrategyRecord('solo')\n"
                "solo.successes, solo.failures, solo.total_tokens = 8, 2, 50000\n"
                "parallel = StrategyRecord('parallel')\n"
                "parallel.successes, parallel.failures, parallel.total_tokens = 15, 5, 200000\n"
                "debate = StrategyRecord('debate')\n"
                "debate.successes, debate.failures, debate.total_tokens = 4, 1, 80000\n"
                "records = [solo, parallel, debate]\n"
                "small = select_strategy(records, 'small')\n"
                "large = select_strategy(records, 'large')\n"
                "assert small.name == 'solo'\n"
                "assert large.name in ('parallel', 'debate')\n"
                "print(f'Small task: {small.name} (score={small.score})')\n"
                "print(f'Large task: {large.name} (score={large.score})')\n"
                "print('PASS: Strategy selector validated')\n"
            ),
        ),
    ],
    "events": [
        CodePattern(
            name="Event Aggregator",
            description="Time-windowed event aggregation with rate tracking",
            source_file="evolved.py", source_repo="arxiv", agos_module="events", priority="medium",
            code_snippet=(
                "import time\nfrom collections import defaultdict\n\n"
                "class EventAggregator:\n"
                "    def __init__(self, window_sec=60):\n"
                "        self.window = window_sec\n"
                "        self.events = defaultdict(list)\n"
                "    def record(self, topic):\n"
                "        self.events[topic].append(time.monotonic())\n"
                "    def rate(self, topic):\n"
                "        now = time.monotonic()\n"
                "        recent = [t for t in self.events[topic] if now - t < self.window]\n"
                "        self.events[topic] = recent\n"
                "        return len(recent)\n"
                "    def top_topics(self, n=5):\n"
                "        rates = {t: self.rate(t) for t in self.events}\n"
                "        return sorted(rates.items(), key=lambda x: -x[1])[:n]\n\n"
                "agg = EventAggregator(window_sec=10)\n"
                "for _ in range(5): agg.record('agent.spawned')\n"
                "for _ in range(3): agg.record('evolution.cycle')\n"
                "agg.record('system.boot')\n"
                "top = agg.top_topics(3)\n"
                "assert top[0][0] == 'agent.spawned' and top[0][1] == 5\n"
                "assert len(top) == 3\n"
                "print(f'Top topics: {top}')\n"
                "print('PASS: Event aggregator validated')\n"
            ),
        ),
    ],
    "coordination": [
        CodePattern(
            name="Message Channel Router",
            description="Topic-based message routing with subscriber matching",
            source_file="evolved.py", source_repo="arxiv", agos_module="coordination", priority="medium",
            code_snippet=(
                "from collections import defaultdict\n\n"
                "class Channel:\n"
                "    def __init__(self, name):\n"
                "        self.name = name\n"
                "        self.subscribers = defaultdict(list)\n"
                "        self.history = []\n"
                "    def subscribe(self, agent, topics):\n"
                "        for t in topics:\n"
                "            self.subscribers[t].append(agent)\n"
                "    def send(self, topic, msg, sender):\n"
                "        self.history.append({'topic': topic, 'msg': msg, 'sender': sender})\n"
                "        receivers = self.subscribers.get(topic, [])\n"
                "        return [r for r in receivers if r != sender]\n"
                "    def broadcast(self, msg, sender):\n"
                "        all_agents = set()\n"
                "        for agents in self.subscribers.values():\n"
                "            all_agents.update(agents)\n"
                "        all_agents.discard(sender)\n"
                "        self.history.append({'topic': '*', 'msg': msg, 'sender': sender})\n"
                "        return sorted(all_agents)\n\n"
                "ch = Channel('team-alpha')\n"
                "ch.subscribe('researcher', ['findings', 'requests'])\n"
                "ch.subscribe('coder', ['requests', 'reviews'])\n"
                "ch.subscribe('reviewer', ['reviews', 'findings'])\n"
                "r1 = ch.send('findings', 'found paper', 'researcher')\n"
                "assert 'reviewer' in r1 and 'researcher' not in r1\n"
                "r2 = ch.broadcast('done', 'coder')\n"
                "assert 'researcher' in r2 and 'reviewer' in r2\n"
                "print(f'findings -> {r1}, broadcast -> {r2}')\n"
                "print('PASS: Message channel router validated')\n"
            ),
        ),
    ],
    "intent.personas": [
        CodePattern(
            name="Adaptive Persona Tuner",
            description="Performance-based persona parameter adjustment",
            source_file="evolved.py", source_repo="arxiv", agos_module="intent.personas", priority="high",
            code_snippet=(
                "class PersonaStats:\n"
                "    def __init__(self, name, budget, max_turns):\n"
                "        self.name = name\n"
                "        self.budget = budget\n"
                "        self.max_turns = max_turns\n"
                "        self.task_results = []\n"
                "    def record(self, success, tokens_used, turns_used):\n"
                "        self.task_results.append({\n"
                "            'success': success, 'tokens': tokens_used, 'turns': turns_used\n"
                "        })\n"
                "    def tune(self):\n"
                "        if len(self.task_results) < 3: return\n"
                "        recent = self.task_results[-5:]\n"
                "        avg_tokens = sum(r['tokens'] for r in recent) / len(recent)\n"
                "        avg_turns = sum(r['turns'] for r in recent) / len(recent)\n"
                "        success_rate = sum(r['success'] for r in recent) / len(recent)\n"
                "        if success_rate < 0.5:\n"
                "            self.budget = int(self.budget * 1.2)\n"
                "            self.max_turns = int(self.max_turns * 1.1)\n"
                "        elif avg_tokens < self.budget * 0.3:\n"
                "            self.budget = int(max(self.budget * 0.85, avg_tokens * 1.5))\n"
                "        return {'budget': self.budget, 'max_turns': self.max_turns}\n\n"
                "p = PersonaStats('researcher', budget=200000, max_turns=30)\n"
                "for s, t, tu in [(True, 50000, 10), (True, 40000, 8), (False, 180000, 28),\n"
                "                  (True, 60000, 12), (True, 55000, 11)]:\n"
                "    p.record(s, t, tu)\n"
                "result = p.tune()\n"
                "assert result['budget'] < 200000\n"
                "print(f'Tuned {p.name}: {result}')\n"
                "print('PASS: Adaptive persona tuner validated')\n"
            ),
        ),
    ],
    "tools": [
        CodePattern(
            name="Tool Composition Planner",
            description="Plans tool chains from capability requirements",
            source_file="evolved.py", source_repo="arxiv", agos_module="tools", priority="medium",
            code_snippet=(
                "class ToolNode:\n"
                "    def __init__(self, name, inputs, outputs):\n"
                "        self.name = name\n"
                "        self.inputs = set(inputs)\n"
                "        self.outputs = set(outputs)\n\n"
                "def plan_chain(tools, needed_output, available_input):\n"
                "    chain = []\n"
                "    current = set(available_input)\n"
                "    remaining = set(needed_output) - current\n"
                "    used = set()\n"
                "    while remaining:\n"
                "        best = None\n"
                "        best_gain = 0\n"
                "        for t in tools:\n"
                "            if t.name in used: continue\n"
                "            if not t.inputs <= current: continue\n"
                "            gain = len(t.outputs & remaining)\n"
                "            if gain > best_gain:\n"
                "                best, best_gain = t, gain\n"
                "        if best is None: break\n"
                "        chain.append(best.name)\n"
                "        current |= best.outputs\n"
                "        remaining -= best.outputs\n"
                "        used.add(best.name)\n"
                "    return chain, len(remaining) == 0\n\n"
                "tools = [\n"
                "    ToolNode('fetch', {'url'}, {'html'}),\n"
                "    ToolNode('parse', {'html'}, {'text', 'links'}),\n"
                "    ToolNode('analyze', {'text'}, {'summary', 'entities'}),\n"
                "    ToolNode('store', {'summary', 'entities'}, {'stored'}),\n"
                "]\n"
                "chain, ok = plan_chain(tools, {'stored'}, {'url'})\n"
                "assert ok and chain == ['fetch', 'parse', 'analyze', 'store']\n"
                "print(f'Tool chain: {chain}, complete: {ok}')\n"
                "print('PASS: Tool composition planner validated')\n"
            ),
        ),
    ],
    "policy.security": [
        CodePattern(
            name="Input Sanitizer",
            description="Pattern-based input sanitization for injection prevention",
            source_file="evolved.py", source_repo="security", agos_module="policy.security", priority="high",
            code_snippet=(
                "import re\n\n"
                "INJECTION_PATTERNS = [\n"
                "    (re.compile(r'__\\w+__'), 'dunder access'),\n"
                "    (re.compile(r'\\bimport\\s+os\\b'), 'os import'),\n"
                "    (re.compile(r'\\bsubprocess\\b'), 'subprocess usage'),\n"
                "    (re.compile(r'\\beval\\s*\\('), 'eval call'),\n"
                "    (re.compile(r'\\bexec\\s*\\('), 'exec call'),\n"
                "    (re.compile(r'\\bopen\\s*\\('), 'file open'),\n"
                "]\n\n"
                "def sanitize_check(text: str) -> list[tuple[str, int]]:\n"
                "    findings = []\n"
                "    for i, line in enumerate(text.splitlines(), 1):\n"
                "        for pat, desc in INJECTION_PATTERNS:\n"
                "            if pat.search(line):\n"
                "                findings.append((desc, i))\n"
                "    return findings\n\n"
                "# Tests\n"
                "clean = 'def add(a, b):\\n    return a + b'\n"
                "assert sanitize_check(clean) == []\n"
                "dirty = 'import os\\nos.system(\"rm -rf /\")'\n"
                "result = sanitize_check(dirty)\n"
                "assert len(result) >= 1\n"
                "assert any('os import' in r[0] for r in result)\n"
                "print(f'Sanitizer found {len(result)} issues in dirty input')\n"
                "print('PASS: Input sanitizer working')\n"
            ),
        ),
        CodePattern(
            name="Anomaly Detector",
            description="Statistical anomaly detection for security event streams",
            source_file="evolved.py", source_repo="security", agos_module="policy.security", priority="medium",
            code_snippet=(
                "import math\nfrom collections import deque\n\n"
                "class AnomalyDetector:\n"
                "    def __init__(self, window: int = 50, threshold: float = 2.5):\n"
                "        self._window = deque(maxlen=window)\n"
                "        self._threshold = threshold\n"
                "        self._alerts: list[dict] = []\n\n"
                "    def observe(self, value: float, label: str = '') -> bool:\n"
                "        self._window.append(value)\n"
                "        if len(self._window) < 10:\n"
                "            return False\n"
                "        mean = sum(self._window) / len(self._window)\n"
                "        variance = sum((x - mean) ** 2 for x in self._window) / len(self._window)\n"
                "        std = math.sqrt(max(variance, 1e-9))\n"
                "        z_score = abs(value - mean) / std\n"
                "        if z_score > self._threshold:\n"
                "            self._alerts.append({'value': value, 'z': round(z_score, 2), 'label': label})\n"
                "            return True\n"
                "        return False\n\n"
                "# Tests\n"
                "det = AnomalyDetector(window=20, threshold=2.0)\n"
                "for i in range(30):\n"
                "    det.observe(10.0 + (i % 3) * 0.1)\n"
                "anomaly = det.observe(100.0, 'spike')\n"
                "assert anomaly is True\n"
                "normal = det.observe(10.0)\n"
                "assert len(det._alerts) >= 1\n"
                "print(f'Alerts: {det._alerts}')\n"
                "print('PASS: Anomaly detector working')\n"
            ),
        ),
    ],
}

# Merge alternates into a unified lookup: module -> list of patterns
_ALL_SNIPPETS: dict[str, list[CodePattern]] = {}
for mod, pat in TESTABLE_SNIPPETS.items():
    _ALL_SNIPPETS[mod] = [pat] + _ALTERNATE_SNIPPETS.get(mod, [])

