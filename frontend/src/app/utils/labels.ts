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
  fulltexts_ready: '全文处理已结束',
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
  conversion: 'PDF 正文提取',
  fulltexts: '全文准备',
  merge: '整篇综合',
  fulltext_paper: '单篇全文处理',
  summarize: '总结',
  report: '报告',
  search: '检索',
  selection: '筛选',
  semantic_ranker: '语义排序',
  ranking: '候选排序',
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
  conversion_io_error: 'PDF 或 Markdown 文件操作失败。',
  invalid_parser_output: 'PDF 解析器返回了无效页面。',
  invalid_page_number: 'PDF 解析器返回了无效页码。',
  parser_failed: 'PDF 正文解析失败。',
  missing_pdf: '没有可用的 PDF 文件。',
  empty_pdf: 'PDF 没有可用页面。',
  encrypted_pdf: 'PDF 已加密，无法读取。',
  scanned_pdf: 'PDF 可能是扫描件（未启用 OCR）。',
  garbled_text: 'PDF 文本无法可靠读取或编码异常。',
  no_extractable_text: 'PDF 没有可用的可提取文本层。',
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
  selector_llm_failure: '筛选模型调用失败。',
  selector_fallback: '已降级为初始排序。',
  QUERY_PLANNING_FAILED: '检索词规划失败，已使用降级方案。',
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

const fallbackReasons: Record<string, string> = {
  dependency_missing: '语义排序依赖未安装，实际使用关键词排序。',
  model_load_failed: '语义模型加载失败，实际使用关键词排序。',
  embedding_failed: '语义向量计算失败，实际使用关键词排序。',
  empty_query: '检索词为空，实际使用关键词排序。',
  no_candidates: '没有可重排的候选论文。',
  semantic_rerank_failed: '语义排序发生异常，实际使用关键词排序。',
};

export function rankingFallbackLabel(reason?: string | null): string {
  if (!reason) return '';
  return fallbackReasons[reason] ?? `语义排序已降级（${reason}）。`;
}

const errorHints: Record<string, string> = {
  no_open_access: '可以核对论文是否有其他公开版本；当前论文会保留为未分析。',
  access_unavailable: '出版方未提供可下载的公开 PDF；可检查其他开放版本。',
  download_timeout: '下载预算已用尽；可稍后重试或查看候选地址。',
  candidate_timeout: '已按顺序尝试其他 PDF 候选。',
  partial_extraction: '当前只按可验证的部分全文处理，不会标成完整全文。',
  conversion_failed: 'PDF 已保留，但正文提取失败；可展开错误详情核对。',
  conversion_io_error: '检查数据目录写入权限和磁盘空间；已下载的 PDF 不会因此删除。',
  parser_failed: 'PDF 已保存，但正文解析器未能提取；可核对 Markdown 与页码范围。',
  invalid_parser_output: '解析器返回的页面结构异常，本次不会伪造页码。',
  invalid_page_number: '解析器页码无效，本次不会伪造页码映射。',
  scanned_pdf: '当前未启用 OCR；该 PDF 只能按不可读全文保留。',
  garbled_text: '文本编码异常，不能作为可验证全文证据。',
  no_extractable_text: 'PDF 没有可提取文本层，不能作为全文证据。',
  invalid_model_output: '展开错误详情查看字段或证据定位；已成功校验的内容会保留。',
  partial_summary: '只有成功读取并校验的分块进入总结，未读部分不会计入。',
  partial_report: '报告只使用实际可用的单篇总结，缺失论文不会被补写。',
  llm_budget: '检查模型输入或请求预算；缩小 Top-k 后重新发起也是一种选择。',
  llm_failed: '检查模型连接与配置；本次不会自动重复计费请求。',
  selector_fallback: '筛选已按配置降级，请查看筛选审计与原因。',
  selector_invalid_output: '筛选输出未通过结构校验；可查看活动记录中的筛选错误。',
  translation_required: '请提供英文检索词，或在设置中配置可用的翻译模型。',
  persist_failed: '检查数据目录写入权限和磁盘空间。',
};

export function errorHint(code?: string | null): string {
  if (!code) return '';
  return errorHints[code] ?? '展开错误详情查看阶段和定位信息。';
}
