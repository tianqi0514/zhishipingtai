const DIRECT_RESPONSE_PATTERNS = [
  /^(你是谁|你是什么(?:助手|模型)?|你叫什么(?:名字)?|请?介绍(?:一下)?你自己|自我介绍)[？?。！!\s]*$/u,
  /^(你好|您好|嗨|哈[喽啰]|hello|hi|hey|在吗|早上好|上午好|下午好|晚上好)[？?。！!\s]*$/iu,
  /^(谢谢|感谢|多谢|辛苦了|再见|拜拜|bye)[你您了啊呀哈？?。！!\s]*$/iu,
  /^(帮助|help|你能做什么|你会什么|怎么用你|如何使用你|使用帮助|有什么功能)[？?。！!\s]*$/iu,
]


export function requiresKnowledgeSearch(input) {
  const query = String(input || '').trim()
  if (!query) return false
  return !DIRECT_RESPONSE_PATTERNS.some(pattern => pattern.test(query))
}


const STRUCTURED_QUERY_PATTERNS = [
  /(?:多少|金额|总额|数量|销量|销售额|排名|最高|最低|平均|合计|统计|同比|环比|增长率|完成率|top\s*\d*|前\s*\d+|实时状态)/iu,
  /(?:sum|count|average|ranking|year[- ]over[- ]year|month[- ]over[- ]month)/iu,
]

const DOCUMENT_EVIDENCE_PATTERNS = [
  /(?:口径|依据|制度|规定|定义|说明|手册|合同|条款|政策|文档)/u,
]

// Questions in this shape ask where a metric is defined, not for a live
// metric value.  Keep these phrases separate from generic metric nouns such
// as “销售额”, which may legitimately appear in a document-only question.
const DOCUMENT_ONLY_QUESTION_PATTERNS = [
  /(?:哪份|哪个|什么)(?:制度|规定|文件|文档|依据|条款)/u,
  /(?:口径|定义)(?:是什么|依据|出自|来自)/u,
  /(?:如何|怎么)(?:定义|规定)/u,
  /(?:统计口径|指标定义).*(?:制度|依据|规定|文件|文档)/u,
]

const QUANTITATIVE_REQUEST_PATTERNS = [
  /(?:多少|为多少|是多少|金额|总额|数量|销量|排名|最高|最低|平均|合计|同比|环比|增长率|完成率|top\s*\d*|前\s*\d+|实时状态|计算|汇总|求和)/iu,
  /(?:sum|count|average|ranking|year[- ]over[- ]year|month[- ]over[- ]month)/iu,
]

const EXPLICIT_METRIC_VALUE_PATTERNS = [
  /(?:多少|金额|总额|数量|销量|销售额|排名|最高|最低|平均|合计|同比|环比|增长率|完成率|top\s*\d*|前\s*\d+|实时状态)/iu,
  /(?:sum|count|average|ranking|year[- ]over[- ]year|month[- ]over[- ]month)/iu,
]

export function requiresStructuredQuery(input) {
  const query = String(input || '').trim()
  if (!query || !requiresKnowledgeSearch(query)) return false
  // A question such as “销售额统计口径依据哪份制度” asks for documentary
  // provenance even though it contains a metric noun.  Explicit value or
  // calculation intent still makes a mixed question require both channels.
  const asksForDocument = DOCUMENT_EVIDENCE_PATTERNS.some(pattern => pattern.test(query))
  const isDocumentOnlyShape = DOCUMENT_ONLY_QUESTION_PATTERNS.some(pattern => pattern.test(query))
  const asksForCalculation = QUANTITATIVE_REQUEST_PATTERNS.some(pattern => pattern.test(query))
  if (asksForDocument && isDocumentOnlyShape && !asksForCalculation) return false
  if (
    asksForDocument
    && !EXPLICIT_METRIC_VALUE_PATTERNS.some(pattern => pattern.test(query))
  ) return false
  return STRUCTURED_QUERY_PATTERNS.some(pattern => pattern.test(query))
}


export function evidenceRequirements(input) {
  const query = String(input || '').trim()
  if (!requiresKnowledgeSearch(query)) return []
  const structured = requiresStructuredQuery(query)
  const document = !structured || DOCUMENT_EVIDENCE_PATTERNS.some(pattern => pattern.test(query))
  return [
    ...(document ? ['knowledge_search'] : []),
    ...(structured ? ['structured_execute_query'] : []),
  ]
}


export function currentUserQuery(events) {
  const message = [...(events || [])].reverse().find(event => (
    event?.type === 'user/message'
    && event?.data?.source?.kind === 'user'
  ))
  const content = message?.data?.content
  if (!Array.isArray(content)) return ''
  return content
    .filter(block => block?.type === 'text')
    .map(block => String(block.text || ''))
    .join('')
    .trim()
}
