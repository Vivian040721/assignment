import { useState } from 'react';
import { AlertCircle, FileJson, FileUp, LoaderCircle } from 'lucide-react';
import type { BenchmarkFile } from '../../../types/contracts';
import { desktopApi } from '../../../services/desktopApi';
import { redactSensitiveData } from '../../../utils/redactSensitive';

type JsonRecord = Record<string, unknown>;
type ModeName = 'sequential' | 'concurrent';
type ModeSummary = { attempts: number | null; mean: number | null; median: number | null };
type PairCheck = { repeat: number; valid: boolean; reason: string };
type BenchmarkReview = {
  expectedRepeats: number | null;
  pairs: PairCheck[];
  synthetic: boolean;
  summaryValidated: boolean;
  conclusionEligible: boolean;
  reasons: string[];
};

const modeLabels: Record<ModeName, string> = { sequential: '串行', concurrent: '并发' };

function record(value: unknown): JsonRecord | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value) ? value as JsonRecord : null;
}

function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function nonEmptyString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function rowsFrom(data: JsonRecord): JsonRecord[] {
  return Array.isArray(data.rows) ? data.rows.map(record).filter((row): row is JsonRecord => row !== null) : [];
}

function modesFrom(data: JsonRecord, rows: JsonRecord[]): Record<ModeName, ModeSummary> {
  const summary = record(data.summary) ?? {};
  const read = (mode: ModeName): ModeSummary => {
    const item = record(summary[mode]) ?? {};
    const rowCount = rows.filter((row) => row.execution_mode === mode).length;
    const attempts = finiteNumber(item.attempts) ?? (rowCount || null);
    return {
      attempts,
      mean: finiteNumber(item.mean_seconds),
      median: finiteNumber(item.median_seconds),
    };
  };
  return { sequential: read('sequential'), concurrent: read('concurrent') };
}

function reviewBenchmark(data: JsonRecord, rows: JsonRecord[]): BenchmarkReview {
  const metadata = record(data.metadata) ?? {};
  const summary = record(data.summary) ?? {};
  const expectedRepeats = finiteNumber(metadata.repeats_per_mode);
  const metadataHash = nonEmptyString(metadata.input_sha256);
  const synthetic = metadata.synthetic === true || metadata.data_mode === 'demo' || rows.some((row) => row.synthetic === true);
  const byRepeat = new Map<number, JsonRecord[]>();

  for (const row of rows) {
    const repeat = finiteNumber(row.repeat);
    if (repeat === null) continue;
    const paired = byRepeat.get(repeat) ?? [];
    paired.push(row);
    byRepeat.set(repeat, paired);
  }

  const pairs: PairCheck[] = [...byRepeat.entries()].sort(([a], [b]) => a - b).map(([repeat, pairedRows]) => {
    const sequential = pairedRows.filter((row) => row.execution_mode === 'sequential');
    const concurrent = pairedRows.filter((row) => row.execution_mode === 'concurrent');
    if (pairedRows.length !== 2 || sequential.length !== 1 || concurrent.length !== 1) {
      return { repeat, valid: false, reason: '该轮没有恰好一条串行和一条并发记录' };
    }

    const [first, second] = [sequential[0], concurrent[0]];
    const firstHash = nonEmptyString(first.input_sha256);
    const secondHash = nonEmptyString(second.input_sha256);
    if (first.inputs_match !== true || second.inputs_match !== true) {
      return { repeat, valid: false, reason: '该轮 inputs_match 未同时确认为 true' };
    }
    if (!firstHash || !secondHash || firstHash !== secondHash || (metadataHash && firstHash !== metadataHash)) {
      return { repeat, valid: false, reason: '该轮输入哈希缺失或不一致' };
    }

    const firstProcessedHash = nonEmptyString(first.processed_input_sha256);
    const secondProcessedHash = nonEmptyString(second.processed_input_sha256);
    if ((firstProcessedHash || secondProcessedHash) && (!firstProcessedHash || firstProcessedHash !== secondProcessedHash)) {
      return { repeat, valid: false, reason: '该轮处理后输入哈希缺失或不一致' };
    }
    return { repeat, valid: true, reason: '输入标记与哈希一致' };
  });

  const reasons: string[] = [];
  if (synthetic) reasons.push('记录标记为合成或演示数据');
  if (expectedRepeats === null) reasons.push('缺少 metadata.repeats_per_mode');
  else if (expectedRepeats < 5) reasons.push(`每种模式记录 ${expectedRepeats} 次，少于 5 次`);
  if (!metadataHash) reasons.push('缺少 metadata.input_sha256');
  if (pairs.length === 0) reasons.push('没有可配对的串行与并发记录');
  const invalidPairs = pairs.filter((pair) => !pair.valid);
  if (invalidPairs.length) reasons.push(`${invalidPairs.length} 轮输入配对未通过哈希检查`);
  if (expectedRepeats !== null && pairs.length !== expectedRepeats) reasons.push(`找到 ${pairs.length} 轮配对，与记录的 ${expectedRepeats} 轮不符`);
  const summaryValidated = summary.performance_validated === true;
  if (!summaryValidated) reasons.push('基准 summary 未标记 performance_validated');

  return {
    expectedRepeats,
    pairs,
    synthetic,
    summaryValidated,
    conclusionEligible: reasons.length === 0 && pairs.length >= 5 && pairs.every((pair) => pair.valid),
    reasons,
  };
}

function formatSeconds(value: number | null): string {
  if (value === null) return '未记录';
  const digits = value > 0 && value < 0.1 ? 3 : 2;
  return `${value.toFixed(digits)} 秒`;
}

function DurationChart({ modes }: { modes: Record<ModeName, ModeSummary> }) {
  const maxMedian = Math.max(modes.sequential.median ?? 0, modes.concurrent.median ?? 0, 0);
  const scale = maxMedian > 0 ? maxMedian : 1;

  return <section className="benchmark-chart" aria-label="串行与并发中位耗时比较">
    <div className="benchmark-chart-head"><div><h3>中位耗时</h3><span>按导入文件 summary 原值显示</span></div><span>耗时 · 秒</span></div>
    <div className="benchmark-bar-grid">
    {(Object.keys(modeLabels) as ModeName[]).map((mode) => {
      const summary = modes[mode];
      const value = summary.median;
      const width = value === null ? 0 : Math.max(3, (value / scale) * 100);
      return <div className="benchmark-bar-row" key={mode}>
        <div className="benchmark-bar-label"><strong>{modeLabels[mode]}</strong><span>{summary.attempts ?? '—'} 次</span><strong className="benchmark-bar-value">{formatSeconds(value)}</strong></div>
        <div className="benchmark-bar-track" role="img" aria-label={`${modeLabels[mode]}中位耗时 ${formatSeconds(value)}`}>
          <div className={`benchmark-bar benchmark-bar-${mode}`} style={{ width: `${width}%` }} />
        </div>
      </div>;
    })}
    </div>
    <div className="benchmark-means">
      <span>串行均值 <strong>{formatSeconds(modes.sequential.mean)}</strong></span>
      <span>并发均值 <strong>{formatSeconds(modes.concurrent.mean)}</strong></span>
    </div>
  </section>;
}

export function MetricsPanel() {
  const [benchmark, setBenchmark] = useState<BenchmarkFile | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      const selected = await desktopApi().loadBenchmark();
      if (selected) setBenchmark(selected);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  };

  const data = benchmark?.data;
  const rows = data ? rowsFrom(data) : [];
  const modes = data ? modesFrom(data, rows) : null;
  const review = data ? reviewBenchmark(data, rows) : null;
  const chartValuesAvailable = Boolean(modes && (modes.sequential.median !== null || modes.concurrent.median !== null));

  return <div className="metrics-view">
    <header className="panel-intro"><div><span className="eyebrow">已有记录</span><h2>性能数据</h2></div></header>
    <p className="panel-subtitle">只读取你选择的已有 JSON 文件；这里不会运行基准测试或调用论文服务。</p>
    <div className="benchmark-import-row">
      <button className="primary-button benchmark-import" type="button" onClick={() => void load()} disabled={loading}>
        {loading ? <LoaderCircle size={15} className="spin"/> : <FileUp size={15}/>}<span>{loading ? '正在读取…' : benchmark ? '重新导入 JSON' : '导入 JSON'}</span>
      </button>
      {benchmark && <span className="file-name" title={benchmark.name}>{benchmark.name}</span>}
    </div>
    {loading && <div className="panel-note" role="status"><LoaderCircle size={14} className="spin"/>正在读取所选文件…</div>}
    {error && <div className="inline-error" role="alert">读取性能记录失败：{error}</div>}
    {benchmark && modes && review && <section className="benchmark-record" aria-label="基准记录">
      <div className={`validity-note ${review.conclusionEligible ? 'validity-cautious' : 'validity-limited'}`}>
        <span>{review.conclusionEligible ? <FileJson size={16}/> : <AlertCircle size={16}/>}</span>
        <p>{review.conclusionEligible
          ? '检查通过：该记录为非合成数据，至少有 5 轮完整配对，且每轮输入标记与哈希一致。这里只并列呈现原始耗时，不推导加速比。'
          : <>仅显示原始测量，不提供真实性能结论。{review.reasons.map((reason) => <span className="benchmark-reason" key={reason}>{reason}。</span>)}</>}
        </p>
      </div>
      <dl className="benchmark-meta">
        <div><dt>文件</dt><dd title={benchmark.name}>{benchmark.name}</dd></div>
        <div><dt>预期轮数</dt><dd>{review.expectedRepeats ?? '未记录'}</dd></div>
        <div><dt>有效配对</dt><dd>{review.pairs.filter((pair) => pair.valid).length} / {review.pairs.length}</dd></div>
        <div><dt>数据标记</dt><dd>{review.synthetic ? '合成 / 演示' : '未标记为合成'}</dd></div>
      </dl>
      {chartValuesAvailable && <DurationChart modes={modes}/>}
      {!chartValuesAvailable && <div className="panel-note benchmark-no-summary"><AlertCircle size={14}/>文件没有 summary.sequential.median_seconds 或 summary.concurrent.median_seconds，无法绘制汇总耗时图。</div>}
      {rows.length > 0 && <details className="benchmark-pairs">
        <summary>逐轮耗时与输入校验（{rows.length} 条）</summary>
        <div className="benchmark-pair-table" role="table" aria-label="逐轮基准耗时">
          <div className="benchmark-pair-header" role="row"><span>轮次</span><span>串行</span><span>并发</span><span>输入校验</span></div>
          {review.pairs.map((pair) => {
            const pairedRows = rows.filter((row) => row.repeat === pair.repeat);
            const durationFor = (mode: ModeName) => formatSeconds(finiteNumber(pairedRows.find((row) => row.execution_mode === mode)?.elapsed_seconds));
            return <div className="benchmark-pair-line" role="row" key={pair.repeat}>
              <span>{pair.repeat}</span><span>{durationFor('sequential')}</span><span>{durationFor('concurrent')}</span>
              <span className={pair.valid ? 'pair-valid' : 'pair-invalid'}>{pair.valid ? '一致' : pair.reason}</span>
            </div>;
          })}
        </div>
      </details>}
      <details className="raw-data"><summary>原始 JSON</summary><pre>{JSON.stringify(redactSensitiveData(benchmark.data), null, 2)}</pre></details>
    </section>}
  </div>;
}
