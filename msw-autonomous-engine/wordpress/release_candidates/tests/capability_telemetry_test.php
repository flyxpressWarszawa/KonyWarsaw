<?php
declare(strict_types=1);
require dirname(__DIR__) . '/CapabilityTelemetry.php';

use MSW\UnifiedEngine\CapabilityTelemetry as T;

$allowed = ['llm.generate', 'knowledge.ingest', 'knowledge.search', 'seo.analyze'];
$checks = 0;

function check($actual, $expected, string $name): void {
    global $checks;
    if ($actual !== $expected) {
        fwrite(STDERR, "FAIL: " . $name . "\nActual: " . var_export($actual, true) . "\nExpected: " . var_export($expected, true) . "\n");
        exit(1);
    }
    ++$checks;
}

$now = 1000;
$first = T::observe([], ['llm.generate', 'knowledge.ingest', 'knowledge.search'], $allowed, $now);
check($first['capabilities'], ['knowledge.ingest', 'knowledge.search', 'llm.generate'], 'full worker advertises three');
$priority = T::observe($first, ['llm.generate'], $allowed, $now + 10);
check($priority['capabilities'], ['knowledge.ingest', 'knowledge.search', 'llm.generate'], 'priority claim does not erase main capabilities');
$expired = T::observe($priority, ['llm.generate'], $allowed, $now + 181);
check($expired['capabilities'], ['llm.generate'], 'stale knowledge capabilities expire');
$recovered = T::observe($expired, ['llm.generate', 'knowledge.ingest'], $allowed, $now + 182);
check($recovered['capabilities'], ['knowledge.ingest', 'llm.generate'], 'main worker restores knowledge capability');
$unknown = T::observe([], ['llm.generate', 'shell.exec', '', 42, null], $allowed, $now);
check($unknown['capabilities'], ['llm.generate'], 'out-of-scope and invalid capabilities ignored');
$legacy = T::observe(['at'=>995,'capabilities'=>['seo.analyze','knowledge.ingest']], ['llm.generate'], $allowed, 1000);
check($legacy['capabilities'], ['knowledge.ingest', 'llm.generate', 'seo.analyze'], 'existing WordPress option state migrates');
$legacyExpired = T::observe(['at'=>5,'capabilities'=>['knowledge.ingest']], ['llm.generate'], $allowed, 1000);
check($legacyExpired['capabilities'], ['llm.generate'], 'old legacy state never grants lasting capabilities');
$future = T::observe(['at'=>2000,'capabilities'=>['knowledge.ingest']], ['llm.generate'], $allowed, 1000);
check($future['capabilities'], ['llm.generate'], 'future timestamps rejected');
$empty = T::observe($first, [], $allowed, $now+12);
check($empty['capabilities'], ['knowledge.ingest', 'knowledge.search', 'llm.generate'], 'empty heartbeat does not erase recent observations');
$long = T::observe($first, ['llm.generate'], $allowed, 1200, 100);
check($long['capabilities'], ['llm.generate'], 'custom TTL obeyed');
try {
    T::observe([], [], $allowed, 1000, 0);
    fwrite(STDERR, "FAIL invalid ttl accepted\n");
    exit(1);
} catch (InvalidArgumentException $e) {
    ++$checks;
}
echo "PASS " . $checks . " capability telemetry cases\n";
