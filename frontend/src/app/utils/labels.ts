const statuses: Record<string, string> = {
  queued: '排队中',
  running: '进行中',
  completed: '已完成',
  partial: '部分完成',
  failed: '失败',
  cancelled: '已取消',
  searching: '检索中',
  searched: '检索完成',
  selected: '已筛选',
  fulltexts_ready: '全文已就绪',
  summarized: '总结完成',
  demo_completed: '演示完成',
  pending: '等待中',
  fulltext_ready: '全文已就绪',
};

const stages: Record<string, string> = {
  selector: '相关性筛选',
  research_summarize: '论文分析',
  research_report: '报告生成',
  main_agent_tool_call: 'Agent 工具调用',
  search_arxiv: 'arXiv 检索',
  search_openalex: 'OpenAlex 检索',
  download: '全文下载',
  convert: 'PDF 正文提取',
  fulltexts: '全文准备',
  summarize: '总结',
  report: '报告',
  search: '检索',
  selection: '筛选',
  total: '总计',
  arxiv_search: 'arXiv 检索',
  openalex_search: 'OpenAlex 检索',
};

const tools: Record<string, string> = {
  search_papers: '检索论文',
  select_top_k_papers: '筛选 Top-k',
  prepare_fulltexts: '准备全文',
  summarize_selected_papers: '总结入选论文',
  generate_research_report: '生成研究报告',
  run_research_pipeline: '完整研究流程',
};

const kinds: Record<string, string> = {
  status: '状态',
  progress: '进度',
  trace: '阶段记录',
  error: '错误',
  chat: '讨论',
  research: '研究',
  tool: '工具',
  message: '消息',
};

const scopes: Record<string, string> = {
  fulltext: '基于全文',
  fulltext_complete: '全文完整',
  partial_fulltext: '部分全文',
  abstract_only: '仅摘要',
  abstract: '仅摘要',
  failed: '总结失败',
  demo_fixture: '演示样例',
};

const paperStatuses: Record<string, string> = {
  selected: '原始入选',
  metadata_only: '仅有元数据',
  downloaded: '全文已下载',
  converted: '全文已提取',
  no_open_access: '无开放全文',
  download_failed: '全文下载失败',
  conversion_failed: '全文提取失败',
  partial_fulltext: '部分全文',
  completed: '已处理',
  analyzed: '已分析',
  fulltext_ready: '全文就绪',
  partial: '部分完成',
  failed: '处理失败',
  missing: '未找到',
};

const errors: Record<string, string> = {
  download_timeout: 'PDF 获取总时长超限，可稍后重试或调大下载预算。',
  candidate_timeout: '这个 PDF 候选超时，已尝试下一个候选。',
  no_open_access: '没有找到可公开获取的全文 PDF。',
  access_unavailable: '出版方拒绝访问该 PDF。',
  http_error: 'PDF 服务返回异常状态。',
  network_error: 'PDF 下载网络异常。',
  dns_error: 'PDF 域名解析失败。',
  unsafe_url: 'PDF 地址未通过安全检查。',
  invalid_pdf: '下载内容不是有效 PDF。',
  pdf_too_large: 'PDF 超过允许的大小。',
  too_many_redirects: 'PDF 重定向次数过多。',
  invalid_redirect: 'PDF 重定向地址无效。',
  storage_error: 'PDF 保存失败。',
  partial_extraction: 'PDF 文本提取不完整，已按部分全文处理。',
  conversion_failed: 'PDF 转 Markdown 失败。',
  missing_pdf: '没有可用的 PDF 文件。',
  empty_pdf: 'PDF 没有可用页面。',
  encrypted_pdf: 'PDF 已加密，无法读取。',
  scanned_pdf: 'PDF 可能是扫描件（未启用 OCR）。',
  invalid_model_output: '模型输出未通过字段、引用或证据校验。',
  partial_summary: '部分源文本分块未成功总结，范围已降级。',
  partial_report: '可用全文总结不足，当前为部分报告。',
  llm_budget: '模型输入或请求预算已用尽。',
  llm_configuration: '模型配置不可用。',
  llm_failed: '模型调用失败。',
  source_unavailable: '没有可用的全文或摘要。',
  invalid_input: '输入无效。',
  no_summaries: '没有可用的单篇总结。',
  persist_failed: '生成文件保存失败。',
  duplicate_summary: '存在重复论文 ID。',
  selector_invalid_output: '筛选结果未通过校验。',
  selector_fallback: '已降级为初始排序。',
  translation_required: '请提供英文检索词或配置翻译模型。',
  rate_limited: '来源服务触发限流。',
  parse_error: '来源返回内容解析失败。',
  invalid_response: '来源响应格式不正确。',
  invalid_record: '来源返回了无效记录。',
  timeout: '请求超时。',
};

export function statusLabel(status?: string): string {
  if (!status) return '未开始';
  return statuses[status] ?? `未知状态（${status}）`;
}

export function stageLabel(stage?: string): string {
  if (!stage) return '任务';
  return stages[stage] ?? `未知阶段（${stage}）`;
}

export function toolLabel(tool?: string): string {
  if (!tool) return '未知工具';
  return tools[tool] ?? `未知工具（${tool}）`;
}

export function kindLabel(kind?: string): string {
  if (!kind) return '记录';
  return kinds[kind] ?? `未知记录（${kind}）`;
}

export function summaryScopeLabel(scope?: string): string {
  if (!scope) return '范围未标注';
  return scopes[scope] ?? `未知范围（${scope}）`;
}

export function paperStatusLabel(status?: string): string {
  if (!status) return '状态未知';
  return paperStatuses[status] ?? `未知状态（${status}）`;
}

export function errorLabel(item: { code?: string | null; message?: string | null }): string {
  if (item.code && errors[item.code]) return errors[item.code];
  return item.message?.trim() || '任务失败。';
}

export function paperErrorLabel(paper: { error?: string | null; errors?: Array<{ code?: string; message?: string }> }): string {
  if (paper.errors?.length) return errorLabel(paper.errors[0]);
  return paper.error ?? '';
}
