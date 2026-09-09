import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent } from 'react';
import {
  BlockquoteRules,
  BoldRules,
  CodeRules,
  HeadingRules,
  HighlightRules,
  HorizontalRuleRules,
  ItalicRules,
  StrikethroughRules,
  SubscriptRules,
  SuperscriptRules,
  UnderlineRules,
} from '@platejs/basic-nodes';
import {
  BlockquotePlugin,
  BoldPlugin,
  CodePlugin,
  H1Plugin,
  H2Plugin,
  H3Plugin,
  H4Plugin,
  H5Plugin,
  H6Plugin,
  HighlightPlugin,
  HorizontalRulePlugin,
  ItalicPlugin,
  KbdPlugin,
  StrikethroughPlugin,
  SubscriptPlugin,
  SuperscriptPlugin,
  UnderlinePlugin,
} from '@platejs/basic-nodes/react';
import {
  FontBackgroundColorPlugin,
  FontColorPlugin,
  FontFamilyPlugin,
  FontSizePlugin,
  LineHeightPlugin,
  TextAlignPlugin,
} from '@platejs/basic-styles/react';
import { CalloutPlugin } from '@platejs/callout/react';
import { CommentPlugin } from '@platejs/comment/react';
import { getCommentKey } from '@platejs/comment';
import { CodeBlockPlugin, CodeLinePlugin } from '@platejs/code-block/react';
import { DatePlugin } from '@platejs/date/react';
import { FootnoteDefinitionPlugin, FootnoteReferencePlugin } from '@platejs/footnote/react';
import { indent, outdent } from '@platejs/indent';
import { IndentPlugin } from '@platejs/indent/react';
import { ColumnItemPlugin, ColumnPlugin } from '@platejs/layout/react';
import { upsertLink } from '@platejs/link';
import { LinkPlugin } from '@platejs/link/react';
import { BulletedListRules, isOrderedList, OrderedListRules, TaskListRules } from '@platejs/list';
import { ListPlugin, useTodoListElement, useTodoListElementState } from '@platejs/list/react';
import { MarkdownPlugin, deserializeMd, serializeMd } from '@platejs/markdown';
import { EquationPlugin, InlineEquationPlugin } from '@platejs/math/react';
import { ImagePlugin } from '@platejs/media/react';
import { MentionPlugin } from '@platejs/mention/react';
import { SlashInputPlugin, SlashPlugin } from '@platejs/slash-command/react';
import {
  TableCellHeaderPlugin,
  TableCellPlugin,
  TablePlugin,
  TableRowPlugin,
} from '@platejs/table/react';
import { TocPlugin, useTocElement, useTocElementState } from '@platejs/toc/react';
import { TogglePlugin, useToggleButton, useToggleButtonState } from '@platejs/toggle/react';
import {
  acceptSuggestion,
  BaseSuggestionPlugin,
  getActiveSuggestionDescriptions,
  getSuggestionKey,
  rejectSuggestion,
} from '@platejs/suggestion';
import { SuggestionPlugin } from '@platejs/suggestion/react';
import { YjsPlugin } from '@platejs/yjs/react';
import { KEYS, TextApi, TrailingBlockPlugin, type Operation, type Value } from 'platejs';
import {
  ParagraphPlugin,
  Plate,
  PlateContent,
  PlateElement,
  PlateLeaf,
  type PlateElementProps,
  type PlateEditor,
  type PlateLeafProps,
  usePlateEditor,
  useEditorRef,
  useEditorSelector,
  usePluginOption,
  type RenderNodeWrapper,
} from 'platejs/react';
import { getEditorDOMFromHtmlString } from 'platejs/static';
import {
  AlignCenter,
  AlignJustify,
  AlignLeft,
  AlignRight,
  ChevronDown,
  ChevronRight,
  Code2,
  FileDown,
  FileUp,
  ImagePlus,
  IndentDecrease,
  IndentIncrease,
  Link2,
  List as ListIcon,
  ListOrdered,
  Minus,
  MoreHorizontal,
  Quote as QuoteIcon,
  Redo2,
  RemoveFormatting,
  Rows3,
  Save as SaveIcon,
  Table2,
  Undo2,
} from 'lucide-react';
import { api } from '../api';
import { cleanEvidenceText } from '../evidence';
import { sha256 } from '../hash';
import { createClientId } from '../ids';
import type { CollaborationAccess, PlateNode, WritingComment, WritingDocument } from '../types/domain';
import { TrustedBlockKit } from './plugins/trusted-blocks';
import { RemoteCursorOverlay } from './RemoteCursorOverlay';
import { SlashInputElement, requestMiaobiEditorCommand, type MiaobiEditorCommand } from './SlashCommandMenu';

export function serverVersionOwnsCollaborativeState(changeSummary?: string | null): boolean {
  const summary = String(changeSummary || '').trim();
  return summary === '输入确认、分析计算与知识约束的一键生成'
    || summary === '应用输入变化并局部更新受影响测算';
}

/**
 * Replace a connected Plate/Yjs document through real Slate operations.
 *
 * `editor.tf.setValue()` only changes the local editor value.  When Yjs is
 * connected the shared XmlText remains unchanged and can immediately restore
 * stale calculated blocks.  Applying remove/insert operations makes the
 * server-authoritative version part of the collaboration event stream.
 */
export function replaceCollaborativeValue(editor: PlateEditor, nextValue: Value): void {
  const currentValue = [...(editor.children as Value)];
  const replacement = JSON.parse(JSON.stringify(nextValue)) as Value;
  const apply = (editor as unknown as { apply: (operation: Operation) => void }).apply.bind(editor);
  editor.tf.withoutNormalizing(() => {
    for (let index = currentValue.length - 1; index >= 0; index -= 1) {
      apply({
        type: 'remove_node',
        path: [index],
        node: currentValue[index],
      });
    }
    replacement.forEach((node, index) => {
      apply({
        type: 'insert_node',
        path: [index],
        node,
      });
    });
  });
}

const Element = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} className={(props.element as Record<string, unknown>).suggestion ? 'block-suggestion' : undefined}>{children}</PlateElement>;
const H1 = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="h1">{children}</PlateElement>;
const H2 = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="h2">{children}</PlateElement>;
const H3 = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="h3">{children}</PlateElement>;
const H4 = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="h4">{children}</PlateElement>;
const H5 = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="h5">{children}</PlateElement>;
const H6 = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="h6">{children}</PlateElement>;
const Quote = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="blockquote">{children}</PlateElement>;
const Strong = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="strong">{children}</PlateLeaf>;
const Emphasis = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="em">{children}</PlateLeaf>;
const Underline = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="u">{children}</PlateLeaf>;
const Strike = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="s">{children}</PlateLeaf>;
const Highlight = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="mark">{children}</PlateLeaf>;
const Code = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="code">{children}</PlateLeaf>;
const Kbd = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="kbd">{children}</PlateLeaf>;
const Subscript = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="sub">{children}</PlateLeaf>;
const Superscript = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="sup">{children}</PlateLeaf>;
const Hr = (props: PlateElementProps) => <PlateElement {...props}><hr contentEditable={false} />{props.children}</PlateElement>;
const Table = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="table"><tbody>{children}</tbody></PlateElement>;
const TableRow = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="tr">{children}</PlateElement>;
const TableCell = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="td">{children}</PlateElement>;
const TableHeader = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="th">{children}</PlateElement>;
const Link = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="a" attributes={{ ...props.attributes, href: String((props.element as Record<string, unknown>).url || '#') }}>{children}</PlateElement>;
const Image = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} className="plate-image"><img src={String((props.element as Record<string, unknown>).url || '')} alt={String((props.element as Record<string, unknown>).caption || '文稿图片')} contentEditable={false} />{children}</PlateElement>;
const Callout = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} className="plate-callout"><span contentEditable={false}>💡</span><div>{children}</div></PlateElement>;
const ColumnGroup = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} className="plate-columns">{children}</PlateElement>;
const Column = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} className="plate-column">{children}</PlateElement>;
const CodeBlock = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="pre" className="plate-code-block">{children}</PlateElement>;
const CodeLine = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="div">{children}</PlateElement>;
const DateElement = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} attributes={{ ...props.attributes, contentEditable: false }} as="time" className="plate-date">{String((props.element as Record<string, unknown>).date || new Date().toISOString().slice(0, 10))}{children}</PlateElement>;
const Equation = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} className="plate-equation"><code contentEditable={false}>{String((props.element as Record<string, unknown>).texExpression || 'E = mc^2')}</code>{children}</PlateElement>;
const InlineEquation = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="span" className="plate-inline-equation"><code contentEditable={false}>{String((props.element as Record<string, unknown>).texExpression || 'x')}</code>{children}</PlateElement>;
const Mention = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} attributes={{ ...props.attributes, contentEditable: false }} as="span" className="plate-mention">@{String((props.element as Record<string, unknown>).value || '成员')}{children}</PlateElement>;
const FootnoteReference = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} attributes={{ ...props.attributes, contentEditable: false }} as="sup" className="plate-footnote">[{String((props.element as Record<string, unknown>).identifier || '')}]{children}</PlateElement>;
const FootnoteDefinition = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} className="plate-footnote-definition">{children}</PlateElement>;
const TocElement = (props: PlateElementProps) => {
  const state = useTocElementState();
  const { props: buttonProps } = useTocElement(state);
  return <PlateElement {...props} className="plate-toc"><div contentEditable={false}><b>目录</b>{state.headingList.length ? state.headingList.map((item) => <button type="button" key={item.id} className={`depth-${item.depth} ${state.activeContentId === item.id ? 'active' : ''}`} aria-current={state.activeContentId === item.id ? 'location' : undefined} onClick={(event) => buttonProps.onClick(event, item, 'smooth')}>{item.title}</button>) : <span>添加标题后自动生成目录</span>}</div>{props.children}</PlateElement>;
};
const Toggle = ({ children, ...props }: PlateElementProps) => {
  const state = useToggleButtonState(String(props.element.id || ''));
  const { buttonProps, open } = useToggleButton(state);
  return <PlateElement {...props} className="plate-toggle"><button type="button" {...buttonProps} contentEditable={false}><ChevronRight className={open ? 'open' : ''} size={15} /></button><div>{children}</div></PlateElement>;
};

const BlockList: RenderNodeWrapper = (props) => {
  if (!props.element.listStyleType) return;
  return (next) => {
    const style = String(next.element.listStyleType);
    if (style === 'todo') return <TodoListItem {...next} />;
    const Tag = isOrderedList(next.element) ? 'ol' : 'ul';
    return <Tag className="plate-list" style={{ listStyleType: style }}><li>{next.children}</li></Tag>;
  };
};

function TodoListItem(props: PlateElementProps) {
  const state = useTodoListElementState({ element: props.element });
  const { checkboxProps } = useTodoListElement(state);
  return <div className="plate-todo"><input type="checkbox" {...checkboxProps} contentEditable={false} /><div className={(props.element as Record<string, unknown>).checked ? 'checked' : ''}>{props.children}</div></div>;
}
const CommentLeaf = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} className="comment-mark">{children}</PlateLeaf>;
const SuggestionLeaf = ({ children, ...props }: PlateLeafProps) => {
  const data = Object.entries(props.leaf as Record<string, unknown>).find(([key]) => key.startsWith(`${KEYS.suggestion}_`))?.[1] as { type?: string } | undefined;
  return <PlateLeaf {...props} className={`suggestion-mark ${data?.type || 'insert'}`}>{children}</PlateLeaf>;
};

export const EditorKit = [
  ParagraphPlugin.withComponent(Element),
  H1Plugin.configure({ inputRules: [HeadingRules.markdown()], node: { component: H1 } }),
  H2Plugin.configure({ inputRules: [HeadingRules.markdown()], node: { component: H2 } }),
  H3Plugin.configure({ inputRules: [HeadingRules.markdown()], node: { component: H3 } }),
  H4Plugin.configure({ inputRules: [HeadingRules.markdown()], node: { component: H4 } }),
  H5Plugin.configure({ inputRules: [HeadingRules.markdown()], node: { component: H5 } }),
  H6Plugin.configure({ inputRules: [HeadingRules.markdown()], node: { component: H6 } }),
  BlockquotePlugin.configure({ inputRules: [BlockquoteRules.markdown()], node: { component: Quote } }),
  HorizontalRulePlugin.configure({ inputRules: [HorizontalRuleRules.markdown({ variant: '-' })], node: { component: Hr } }),
  BoldPlugin.configure({ inputRules: [BoldRules.markdown({ variant: '*' })], node: { component: Strong } }),
  ItalicPlugin.configure({ inputRules: [ItalicRules.markdown({ variant: '*' })], node: { component: Emphasis } }),
  UnderlinePlugin.configure({ inputRules: [UnderlineRules.markdown()], node: { component: Underline } }),
  StrikethroughPlugin.configure({ inputRules: [StrikethroughRules.markdown()], node: { component: Strike } }),
  HighlightPlugin.configure({ inputRules: [HighlightRules.markdown({ variant: '==' })], node: { component: Highlight } }),
  CodePlugin.configure({ inputRules: [CodeRules.markdown()], node: { component: Code } }),
  SubscriptPlugin.configure({ inputRules: [SubscriptRules.markdown()], node: { component: Subscript } }),
  SuperscriptPlugin.configure({ inputRules: [SuperscriptRules.markdown()], node: { component: Superscript } }),
  KbdPlugin.withComponent(Kbd),
  FontColorPlugin, FontBackgroundColorPlugin, FontFamilyPlugin, FontSizePlugin,
  TextAlignPlugin.configure({ inject: { nodeProps: { defaultNodeValue: 'start', nodeKey: 'align', styleKey: 'textAlign', validNodeValues: ['start', 'left', 'center', 'right', 'end', 'justify'] }, targetPlugins: [...KEYS.heading, KEYS.p, KEYS.blockquote] } }),
  LineHeightPlugin.configure({ inject: { nodeProps: { defaultNodeValue: 1.75, validNodeValues: [1, 1.25, 1.5, 1.75, 2, 2.5, 3] }, targetPlugins: [...KEYS.heading, KEYS.p] } }),
  IndentPlugin.configure({ inject: { targetPlugins: [...KEYS.heading, KEYS.p, KEYS.blockquote, KEYS.codeBlock, KEYS.toggle, KEYS.img] }, options: { offset: 24 } }),
  LinkPlugin.withComponent(Link),
  TablePlugin.withComponent(Table), TableRowPlugin.withComponent(TableRow),
  TableCellPlugin.withComponent(TableCell), TableCellHeaderPlugin.withComponent(TableHeader),
  CalloutPlugin.withComponent(Callout), ColumnPlugin.withComponent(ColumnGroup), ColumnItemPlugin.withComponent(Column),
  CodeBlockPlugin.withComponent(CodeBlock), CodeLinePlugin.withComponent(CodeLine),
  DatePlugin.withComponent(DateElement), EquationPlugin.withComponent(Equation), InlineEquationPlugin.withComponent(InlineEquation),
  ImagePlugin.configure({ options: { disableUploadInsert: true }, node: { component: Image } }),
  MentionPlugin.withComponent(Mention), TocPlugin.withComponent(TocElement),
  TogglePlugin.withComponent(Toggle),
  FootnoteReferencePlugin.withComponent(FootnoteReference), FootnoteDefinitionPlugin.withComponent(FootnoteDefinition),
  ListPlugin.configure({
    inputRules: [BulletedListRules.markdown({ variant: '-' }), OrderedListRules.markdown({ variant: '.' }), TaskListRules.markdown({ checked: false }), TaskListRules.markdown({ checked: true })],
    inject: { nodeProps: { nodeKey: KEYS.listType, query: ({ nodeProps }) => Boolean(nodeProps.element?.listStyleType && !isOrderedList(nodeProps.element)), transformProps: ({ props }) => ({ ...props, role: 'listitem', style: { ...props.style, display: 'list-item' } }) }, targetPlugins: [...KEYS.heading, KEYS.p, KEYS.blockquote, KEYS.codeBlock, KEYS.toggle, KEYS.img] },
    render: { belowNodes: BlockList },
  }),
  SlashPlugin.configure({
    options: {
      triggerQuery: (editor) => !editor.api.some({ match: { type: editor.getType(KEYS.codeBlock) } }),
    },
  }),
  SlashInputPlugin.withComponent(SlashInputElement),
  MarkdownPlugin.configure({ options: { plainMarks: [KEYS.suggestion, KEYS.comment] } }),
  TrailingBlockPlugin,
  ...TrustedBlockKit,
];

const EMPTY_VALUE: Value = [
  { id: 'title', type: KEYS.h1, children: [{ text: '应急处置方案' }] },
  { id: 'intro', type: KEYS.p, children: [{ text: '请从左侧目录选择章节，或使用右侧妙笔助手生成有依据的草稿。' }] },
];

export function normalizeCollaborativeValue(value: Value): { value: Value; changed: boolean } {
  let changed = false;
  const normalizeNode = (node: Record<string, unknown>): Record<string, unknown> => {
    const children = Array.isArray(node.children)
      ? (node.children as Array<Record<string, unknown>>).map(normalizeNode)
      : undefined;
    if (node.type !== 'knowledge_citation' || !children) {
      return children ? { ...node, children } : { ...node };
    }
    const normalizedChildren = children.map((child) => {
      if (typeof child.text !== 'string') return child;
      const text = cleanEvidenceText(child.text);
      if (text !== child.text) changed = true;
      return { ...child, text };
    });
    return { ...node, children: normalizedChildren };
  };
  const normalized = value.map((node) => {
    const item = normalizeNode(node as Record<string, unknown>);
    if (item.type !== 'knowledge_citation') return item;
    changed = true;
    const text = Array.isArray(item.children)
      ? (item.children as Array<Record<string, unknown>>).map((child) => String(child.text || '')).join('')
      : '';
    const reference = { ...item, children: [{ text: '' }] };
    return {
      id: `${String(item.id || 'citation')}-paragraph`,
      type: KEYS.p,
      children: [...(text ? [{ text: `${text} ` }] : []), reference],
    };
  });
  return { value: normalized as Value, changed };
}

type Props = {
  document: WritingDocument;
  onSaved: (document: WritingDocument) => void;
  onDirtyChange: (dirty: boolean) => void;
  onRequestSource: (tab: 'assistant' | 'evidence' | 'calculation' | 'review') => void;
  onAgentEdit?: (request: { action: string; originalText: string; blockId?: string; instruction?: string }) => Promise<{ id: string; text: string }>;
  onAgentEditDecision?: (editId: string, decision: 'accept' | 'reject') => Promise<unknown>;
  onAgentActivity?: () => void;
  insertionRequest?: PlateNode | PlateNode[] | MarkdownSuggestionInsertion | null;
  onInserted?: () => void;
};

export type MarkdownSuggestionInsertion = {
  kind: 'markdown-suggestion';
  markdown: string;
  references: PlateNode[];
};

function isMarkdownSuggestion(value: Props['insertionRequest']): value is MarkdownSuggestionInsertion {
  return Boolean(value && !Array.isArray(value) && (value as MarkdownSuggestionInsertion).kind === 'markdown-suggestion');
}

/**
 * Convert the assistant's Markdown into native Plate nodes and replace only
 * validated citation labels with the small inline citation element. This
 * prevents Markdown control characters from leaking into the formal body.
 */
export function materializeMarkdownSuggestion(editor: PlateEditor, request: MarkdownSuggestionInsertion): Value {
  const references = new Map<string, PlateNode[]>();
  request.references.forEach((reference) => {
    const label = String(reference.citation_label || '');
    references.set(label, [...(references.get(label) || []), reference]);
  });
  const expand = (node: PlateNode): PlateNode[] => {
    if (typeof node.text === 'string') {
      const output: PlateNode[] = [];
      let cursor = 0;
      for (const match of node.text.matchAll(/\[(\d+)\]/g)) {
        const index = match.index ?? 0;
        const label = match[0];
        const queue = references.get(label);
        const reference = queue?.shift();
        if (!reference) continue;
        if (index > cursor) output.push({ ...node, text: node.text.slice(cursor, index) });
        output.push(reference);
        cursor = index + label.length;
      }
      if (cursor === 0) return [node];
      if (cursor < node.text.length) output.push({ ...node, text: node.text.slice(cursor) });
      return output;
    }
    const children = Array.isArray(node.children)
      ? node.children.flatMap((child) => expand(child as PlateNode))
      : [{ text: '' }];
    return [{ ...node, children }];
  };
  const value = deserializeMd(editor, request.markdown) as Value;
  return value.flatMap((node) => expand(node as PlateNode)).map((node) => ({
    ...node,
    id: typeof node.id === 'string' ? node.id : createClientId(),
    suggestion: {
      id: createClientId(),
      type: 'insert',
      userId: 'miaobi-agent',
      createdAt: Date.now(),
    },
  })) as unknown as Value;
}

export function MiaobiEditor({ document, onSaved, onDirtyChange, onRequestSource, onAgentEdit, onAgentEditDecision, onAgentActivity, insertionRequest, onInserted }: Props) {
  const initial = normalizeCollaborativeValue(
    (document.current_version?.content?.length ? document.current_version.content : EMPTY_VALUE) as Value,
  ).value;
  const [collaboration, setCollaboration] = useState<CollaborationAccess | null>(null);
  const [collaborationError, setCollaborationError] = useState('');
  const [collaborationAttempt, setCollaborationAttempt] = useState(0);
  const [editorReady, setEditorReady] = useState(false);
  const [comments, setComments] = useState<WritingComment[]>([]);
  const [commentText, setCommentText] = useState('');
  const [commenting, setCommenting] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [insertDialog, setInsertDialog] = useState<'link' | 'image' | null>(null);
  const [insertValue, setInsertValue] = useState('');
  const [moreOpen, setMoreOpen] = useState(false);
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; text: string; blockId?: string } | null>(null);
  const [agentEdit, setAgentEdit] = useState<{ id: string; text: string; action: string } | null>(null);
  const [agentEditError, setAgentEditError] = useState('');
  const [agentEditing, setAgentEditing] = useState('');
  const selectionRef = useRef<unknown>(null);
  const importRef = useRef<HTMLInputElement>(null);
  const cursorColor = useMemo(() => `hsl(${[...document.id].reduce((sum, value) => sum + value.charCodeAt(0), 0) % 360} 66% 46%)`, [document.id]);
  const editor = usePlateEditor({
    // Plate uses editor.meta.userId to scope temporary slash/mention input
    // nodes to the local collaborator.
    userId: collaboration?.user.id,
    plugins: [
      ...EditorKit,
      CommentPlugin.withComponent(CommentLeaf),
      SuggestionPlugin.configure({ options: { currentUserId: collaboration?.user.id || null, isSuggesting: suggesting } }).withComponent(SuggestionLeaf),
      ...(collaboration ? [YjsPlugin.configure({
        options: {
          userId: collaboration.user.id,
          cursors: { data: { color: cursorColor, name: collaboration.user.name } },
          providers: [
            {
              type: 'hocuspocus',
              options: {
                name: collaboration.room,
                url: collaboration.url,
                token: async () => (await api<CollaborationAccess>(`/writing/documents/${document.id}/collaboration-token`, { method: 'POST' })).token,
              },
            },
            ...(typeof indexedDB === 'undefined' ? [] : [{ type: 'indexeddb' as const, options: { docName: collaboration.room } }]),
          ],
        },
        render: { afterEditable: RemoteCursorOverlay },
      })] : []),
    ],
    value: undefined,
    skipInitialization: true,
  }, [document.id, collaboration?.token]);
  const [saving, setSaving] = useState(false);
  const [lastSavedHash, setLastSavedHash] = useState(document.current_version?.content_hash || '');
  const valueRef = useRef<Value>(initial);
  const saveTimer = useRef<number | undefined>(undefined);

  const save = async (summary = '自动保存') => {
    if (saving) return;
    setSaving(true);
    try {
      const version = await api<Record<string, unknown>>(`/writing/documents/${document.id}/versions`, {
        method: 'POST',
        body: { content: valueRef.current, change_summary: summary, publish: false },
      });
      setLastSavedHash(String(version.content_hash || ''));
      onDirtyChange(false);
      onSaved({ ...document, current_version: version as WritingDocument['current_version'] });
    } finally {
      setSaving(false);
    }
  };

  const openAgentContextMenu = (event: ReactMouseEvent<HTMLDivElement>) => {
    if (!onAgentEdit || collaboration?.read_only || !editor.api.isExpanded() || !editor.selection) return;
    const text = editor.api.string(editor.selection).trim();
    if (!text) return;
    const entry = editor.api.block({ highest: true });
    const block = entry?.[0] as Record<string, unknown> | undefined;
    if (block && ['computed_metric', 'inference_conclusion', 'verified_fact', 'alternative_plan'].includes(String(block.type || ''))) return;
    event.preventDefault();
    selectionRef.current = JSON.parse(JSON.stringify(editor.selection));
    setAgentEdit(null);
    setAgentEditError('');
    setContextMenu({ x: Math.min(event.clientX, window.innerWidth - 220), y: Math.min(event.clientY, window.innerHeight - 320), text, blockId: String(block?.id || '') || undefined });
  };

  const requestAgentRevision = async (action: string) => {
    if (!contextMenu || !onAgentEdit || agentEditing) return;
    setAgentEditing(action);
    setAgentEditError('');
    onAgentActivity?.();
    try {
      const result = await onAgentEdit({ action, originalText: contextMenu.text, blockId: contextMenu.blockId });
      setAgentEdit({ ...result, action });
    } catch (reason) {
      setAgentEditError(reason instanceof Error ? reason.message : '修改建议生成失败');
    } finally { setAgentEditing(''); }
  };

  const decideAgentRevision = async (decision: 'accept' | 'reject') => {
    if (!agentEdit || !contextMenu) return;
    setAgentEditing(decision);
    try {
      await onAgentEditDecision?.(agentEdit.id, decision);
      if (decision === 'accept' && selectionRef.current) {
        editor.getApi(BaseSuggestionPlugin).suggestion.withoutSuggestions(() => {
          editor.tf.select(selectionRef.current as never);
          editor.tf.insertText(agentEdit.text);
        });
        editor.tf.focus();
        onDirtyChange(true);
      }
      setContextMenu(null);
      setAgentEdit(null);
      selectionRef.current = null;
    } catch (reason) {
      setAgentEditError(reason instanceof Error ? reason.message : '修改建议处理失败');
    } finally { setAgentEditing(''); }
  };

  const refreshComments = () => api<WritingComment[]>(`/writing/documents/${document.id}/comments`).then(setComments);

  useEffect(() => {
    let active = true;
    setCollaboration(null);
    setCollaborationError('');
    Promise.all([
      api<CollaborationAccess>(`/writing/documents/${document.id}/collaboration-token`, { method: 'POST' }),
      refreshComments(),
    ]).then(([access]) => { if (active) setCollaboration(access); })
      .catch((reason) => { if (active) setCollaborationError(reason instanceof Error ? reason.message : '协同服务连接失败'); });
    return () => { active = false; };
  }, [document.id, collaborationAttempt]);

  useEffect(() => {
    if (!collaboration) return;
    let active = true;
    setEditorReady(false);
    void editor.getApi(YjsPlugin).yjs.init({
      id: collaboration.room,
      autoSelect: 'end',
      value: initial,
      onReady: async () => {
        if (!active) return;
        let synchronized = normalizeCollaborativeValue(editor.children as Value);
        const currentVersion = document.current_version;
        let replacedByServer = false;
        if (
          !collaboration.read_only
          && currentVersion?.content_hash
          && serverVersionOwnsCollaborativeState(currentVersion.change_summary)
          && await sha256(synchronized.value) !== currentVersion.content_hash
        ) {
          // Deterministic recomputation and one-click generation create an
          // immutable server version.  That version must replace an older Yjs
          // snapshot; otherwise the editor can display stale calculated data.
          // Ordinary autosaves do not enter this branch, so offline edits keep
          // their normal Yjs recovery semantics.
          synchronized = normalizeCollaborativeValue(initial);
          replaceCollaborativeValue(editor, synchronized.value);
          replacedByServer = true;
        }
        if (synchronized.changed && !collaboration.read_only && !replacedByServer) {
          replaceCollaborativeValue(editor, synchronized.value);
          valueRef.current = synchronized.value;
          window.setTimeout(() => void save('协同文稿引用格式迁移'), 0);
        } else {
          valueRef.current = synchronized.value;
        }
        if (replacedByServer) {
          // Persist an ordinary version after the shared document catches up,
          // so later reloads no longer need to treat this server version as an
          // outstanding collaboration override.
          window.setTimeout(() => void save('同步推演更新到协作文稿'), 0);
        }
        setEditorReady(true);
      },
    }).catch((reason: unknown) => {
      if (active) setCollaborationError(reason instanceof Error ? reason.message : '协同文稿初始化失败');
    });
    return () => {
      active = false;
      editor.getApi(YjsPlugin).yjs.destroy();
    };
  }, [editor, collaboration?.room]);

  useEffect(() => () => window.clearTimeout(saveTimer.current), []);

  useEffect(() => {
    const onCommand = (event: Event) => {
      const command = (event as CustomEvent<{ command?: MiaobiEditorCommand }>).detail?.command;
      if (command === 'assistant') onRequestSource('assistant');
      if (command === 'evidence') onRequestSource('evidence');
      if (command === 'calculation' || command === 'inference') onRequestSource('calculation');
      if (command === 'image') {
        setInsertValue('');
        setInsertDialog('image');
      }
    };
    window.addEventListener('miaobi:editor-command', onCommand);
    return () => window.removeEventListener('miaobi:editor-command', onCommand);
  }, [onRequestSource]);

  useEffect(() => {
    if (!insertionRequest || collaboration?.read_only) return;
    const nodes = isMarkdownSuggestion(insertionRequest)
      ? materializeMarkdownSuggestion(editor, insertionRequest)
      : insertionRequest;
    editor.tf.insertNodes(nodes as Value[number] | Value);
    onInserted?.();
  }, [editor, insertionRequest, onInserted, collaboration?.read_only]);

  const tools = useMemo(() => [
    ['正文', KEYS.p], ['标题 1', KEYS.h1], ['标题 2', KEYS.h2], ['标题 3', KEYS.h3],
    ['标题 4', KEYS.h4], ['标题 5', KEYS.h5], ['标题 6', KEYS.h6], ['引用段落', KEYS.blockquote],
  ] as const, []);

  const importDocument = async (file?: File) => {
    if (!file || collaboration?.read_only) return;
    const suffix = file.name.toLowerCase().split('.').pop();
    try {
      let nodes: Value = [];
      if (suffix === 'docx') {
        const { importDocx } = await import('@platejs/docx-io');
        nodes = (await importDocx(editor, await file.arrayBuffer())).nodes as Value;
      } else {
        const content = await file.text();
        if (suffix === 'html' || suffix === 'htm') {
          nodes = editor.api.html.deserialize({ element: getEditorDOMFromHtmlString(content) }) as Value;
        } else {
          nodes = deserializeMd(editor, content);
        }
      }
      if (!nodes.length) throw new Error('文件中没有可导入内容');
      editor.tf.insertNodes(nodes);
      editor.tf.focus();
    } catch (reason) {
      setCollaborationError(reason instanceof Error ? `导入失败：${reason.message}` : '文档导入失败');
    } finally {
      if (importRef.current) importRef.current.value = '';
    }
  };

  const exportMarkdown = () => {
    const markdown = serializeMd(editor, { value: editor.children });
    const href = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown;charset=utf-8' }));
    const link = window.document.createElement('a');
    link.href = href;
    link.download = `${document.title || '妙笔文稿'}.md`;
    link.click();
    URL.revokeObjectURL(href);
  };

  const submitInsertDialog = () => {
    const value = insertValue.trim();
    if (!value) return;
    let url: URL;
    try {
      url = new URL(value, window.location.origin);
      if (!['http:', 'https:', 'mailto:'].includes(url.protocol)) throw new Error('unsupported');
    } catch {
      setCollaborationError('只允许使用 HTTP、HTTPS 或邮件链接');
      return;
    }
    if (insertDialog === 'link') {
      upsertLink(editor, { url: url.toString(), text: editor.api.string(editor.selection || []) || url.toString(), target: '_blank' });
    } else {
      editor.tf.insertNodes({ type: KEYS.img, url: url.toString(), caption: '', children: [{ text: '' }] });
    }
    setInsertDialog(null);
    setInsertValue('');
    editor.tf.focus();
  };

  const tableAction = (action: 'insert' | 'row-after' | 'column-after' | 'delete-row' | 'delete-column' | 'merge' | 'split' | 'delete') => {
    const tf = editor.getTransforms(TablePlugin);
    if (action === 'insert') tf.insert.table({ colCount: 3, rowCount: 3 }, { select: true });
    if (action === 'row-after') tf.insert.tableRow();
    if (action === 'column-after') tf.insert.tableColumn();
    if (action === 'delete-row') tf.remove.tableRow();
    if (action === 'delete-column') tf.remove.tableColumn();
    if (action === 'merge') tf.table.merge();
    if (action === 'split') tf.table.split();
    if (action === 'delete') tf.remove.table();
    editor.tf.focus();
  };

  const toggleSuggestionMode = () => {
    const next = !suggesting;
    setSuggesting(next);
    editor.setOption(BaseSuggestionPlugin, 'isSuggesting', next);
  };

  const decideSuggestion = (accept: boolean) => {
    const blockEntry = editor.getApi(BaseSuggestionPlugin).suggestion.node();
    if (blockEntry && editor.getApi(BaseSuggestionPlugin).suggestion.isBlockSuggestion(blockEntry[0])) {
      const [node, path] = blockEntry;
      const data = (node as Record<string, unknown>).suggestion as { id: string; type: 'insert' | 'remove'; userId: string };
      const resolved = { createdAt: new Date(), keyId: KEYS.suggestion, suggestionId: data.id, type: data.type, userId: data.userId } as const;
      editor.getApi(BaseSuggestionPlugin).suggestion.withoutSuggestions(() => {
        (accept ? acceptSuggestion : rejectSuggestion)(editor, resolved);
      });
      return;
    }
    const active = getActiveSuggestionDescriptions(editor)[0];
    if (!active) return;
    const resolved = {
      createdAt: new Date(),
      keyId: getSuggestionKey(active.suggestionId),
      suggestionId: active.suggestionId,
      type: active.type === 'insertion' ? 'insert' : active.type === 'deletion' ? 'remove' : 'replace',
      userId: active.userId,
      newText: 'insertedText' in active ? active.insertedText : undefined,
      text: 'deletedText' in active ? active.deletedText : undefined,
    } as const;
    editor.getApi(BaseSuggestionPlugin).suggestion.withoutSuggestions(() => {
      (accept ? acceptSuggestion : rejectSuggestion)(editor, resolved);
    });
  };

  const createComment = async () => {
    if (!commentText.trim() || commenting || !editor.selection) return;
    const selection = editor.selection;
    setCommenting(true);
    try {
      const row = await api<WritingComment>(`/writing/documents/${document.id}/comments`, { method: 'POST', body: { content: commentText.trim() } });
      editor.tf.setNodes({ [KEYS.comment]: true, [getCommentKey(row.id)]: true }, { at: selection, match: TextApi.isText, split: true });
      setComments((current) => [...current, row]);
      setCommentText('');
    } catch (reason) {
      setCollaborationError(reason instanceof Error ? reason.message : '评论添加失败');
    } finally {
      setCommenting(false);
    }
  };

  const resolveComment = async (row: WritingComment) => {
    try {
      const rows = await api<WritingComment[]>(`/writing/comments/${row.id}/resolve`, { method: 'POST', body: { resolved: row.status === 'open' } });
      if (row.status === 'open') editor.getTransforms(CommentPlugin).comment.unsetMark({ id: row.id });
      setComments((current) => current.map((item) => rows.find((next) => next.id === item.id) || item));
    } catch (reason) {
      setCollaborationError(reason instanceof Error ? reason.message : '评论状态更新失败');
    }
  };

  if (!collaboration) {
    return <div className="editor-shell editor-loading" data-testid="plate-editor"><LoaderLabel error={collaborationError} onRetry={() => setCollaborationAttempt((value) => value + 1)} /></div>;
  }

  return (
    <div className="editor-shell" data-testid="plate-editor">
      <div className={`editor-toolbar ${collaboration?.read_only ? 'read-only' : ''}`} role="toolbar" aria-label="文稿编辑工具">
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.undo()} title="撤销" aria-label="撤销"><Undo2 size={16} /></button>
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.redo()} title="重做" aria-label="重做"><Redo2 size={16} /></button>
        <span className="toolbar-divider" />
        <label className="toolbar-select"><span className="sr-only">段落样式</span><select aria-label="段落样式" disabled={collaboration?.read_only} defaultValue={KEYS.p} onChange={(event) => { editor.tf.setNodes({ type: event.target.value, indent: undefined, listStyleType: undefined }); editor.tf.focus(); }}>{tools.map(([label, type]) => <option value={type} key={type}>{label}</option>)}</select><ChevronDown size={13} /></label>
        <label className="toolbar-select font-family"><span className="sr-only">字体</span><select aria-label="字体" disabled={collaboration?.read_only} defaultValue="" onChange={(event) => editor.tf.addMark('fontFamily', event.target.value || undefined)}><option value="">默认字体</option><option value="SimSun, STSong, serif">宋体</option><option value="FangSong, STFangsong, serif">仿宋</option><option value="SimHei, STHeiti, sans-serif">黑体</option><option value="KaiTi, STKaiti, serif">楷体</option><option value="Inter, PingFang SC, sans-serif">现代黑体</option></select><ChevronDown size={13} /></label>
        <label className="toolbar-select font-size"><span className="sr-only">字号</span><select aria-label="字号" disabled={collaboration?.read_only} defaultValue="" onChange={(event) => editor.tf.addMark('fontSize', event.target.value || undefined)}><option value="">字号</option><option value="12px">小五</option><option value="14px">五号</option><option value="16px">小四</option><option value="18px">四号</option><option value="22px">小二</option><option value="26px">二号</option></select><ChevronDown size={13} /></label>
        <span className="toolbar-divider" />
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.toggleMark(KEYS.bold)} title="粗体"><b>B</b></button>
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.toggleMark(KEYS.italic)} title="斜体"><i>I</i></button>
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.toggleMark(KEYS.underline)} title="下划线"><u>U</u></button>
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.toggleMark(KEYS.strikethrough)} title="删除线"><s>S</s></button>
        <label className="toolbar-color" title="文字颜色"><input aria-label="文字颜色" type="color" disabled={collaboration?.read_only} onChange={(event) => { editor.tf.addMark('color', event.target.value); editor.tf.focus(); }} /><span>A</span></label>
        <label className="toolbar-color highlight-color" title="背景颜色"><input aria-label="背景颜色" type="color" defaultValue="#fff2a8" disabled={collaboration?.read_only} onChange={(event) => { editor.tf.addMark('backgroundColor', event.target.value); editor.tf.focus(); }} /><span>▰</span></label>
        <span className="toolbar-divider" />
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => { editor.tf.setNodes({ align: 'left' }); editor.tf.focus(); }} title="左对齐"><AlignLeft size={16} /></button>
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => { editor.tf.setNodes({ align: 'center' }); editor.tf.focus(); }} title="居中"><AlignCenter size={16} /></button>
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => { editor.tf.setNodes({ align: 'right' }); editor.tf.focus(); }} title="右对齐"><AlignRight size={16} /></button>
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => { editor.tf.setNodes({ align: 'justify' }); editor.tf.focus(); }} title="两端对齐"><AlignJustify size={16} /></button>
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => outdent(editor)} title="减少缩进"><IndentDecrease size={16} /></button>
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => indent(editor)} title="增加缩进"><IndentIncrease size={16} /></button>
        <label className="toolbar-select line-height"><span className="sr-only">行距</span><select aria-label="行距" disabled={collaboration?.read_only} defaultValue="1.75" onChange={(event) => { editor.tf.setNodes({ lineHeight: Number(event.target.value) }); editor.tf.focus(); }}><option value="1">1.0</option><option value="1.25">1.25</option><option value="1.5">1.5</option><option value="1.75">1.75</option><option value="2">2.0</option><option value="2.5">2.5</option></select><ChevronDown size={13} /></label>
        <span className="toolbar-divider" />
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => { editor.tf.setNodes({ type: KEYS.p, indent: 1, listStyleType: 'disc' }); editor.tf.focus(); }} title="无序列表"><ListIcon size={16} /></button>
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => { editor.tf.setNodes({ type: KEYS.p, indent: 1, listStyleType: 'decimal' }); editor.tf.focus(); }} title="有序列表"><ListOrdered size={16} /></button>
        <div className="toolbar-menu"><button type="button" disabled={collaboration?.read_only} onClick={() => setMoreOpen((value) => !value)} title="插入与表格"><Table2 size={16} /><ChevronDown size={12} /></button>{moreOpen && <div className="toolbar-menu-popover"><b>插入</b><button type="button" onClick={() => { tableAction('insert'); setMoreOpen(false); }}><Table2 size={15} />3×3 表格</button><button type="button" onClick={() => requestMiaobiEditorCommand('image')}><ImagePlus size={15} />图片</button><button type="button" onClick={() => { setInsertValue(''); setInsertDialog('link'); setMoreOpen(false); }}><Link2 size={15} />链接</button><button type="button" onClick={() => { editor.tf.setNodes({ type: KEYS.blockquote }); setMoreOpen(false); }}><QuoteIcon size={15} />引用段落</button><button type="button" onClick={() => { editor.tf.insertNodes({ type: KEYS.hr, children: [{ text: '' }] }); setMoreOpen(false); }}><Minus size={15} />分隔线</button><b>当前表格</b><button type="button" onClick={() => tableAction('row-after')}><Rows3 size={15} />新增一行</button><button type="button" onClick={() => tableAction('column-after')}><Table2 size={15} />新增一列</button><button type="button" onClick={() => tableAction('merge')}>合并单元格</button><button type="button" onClick={() => tableAction('split')}>拆分单元格</button><button type="button" onClick={() => tableAction('delete-row')}>删除行</button><button type="button" onClick={() => tableAction('delete-column')}>删除列</button><button type="button" className="danger-item" onClick={() => tableAction('delete')}>删除表格</button></div>}</div>
        <button type="button" disabled={collaboration?.read_only} onClick={() => importRef.current?.click()} title="导入 Word、Markdown 或 HTML"><FileUp size={16} /></button>
        <input ref={importRef} type="file" hidden accept=".docx,.md,.mdx,.txt,.html,.htm" onChange={(event) => void importDocument(event.target.files?.[0])} />
        <button type="button" disabled={collaboration?.read_only} onClick={exportMarkdown} title="导出 Markdown"><FileDown size={16} /></button>
        <button type="button" disabled={collaboration?.read_only} onMouseDown={(event) => event.preventDefault()} onClick={() => { editor.tf.setNodes({ bold: undefined, italic: undefined, underline: undefined, strikethrough: undefined, color: undefined, backgroundColor: undefined, fontFamily: undefined, fontSize: undefined }, { match: TextApi.isText, split: true }); }} title="清除文字格式"><RemoveFormatting size={16} /></button>
        <span className="toolbar-divider" />
        <button type="button" disabled={collaboration?.read_only} onClick={() => onRequestSource('evidence')} title="从锁定知识版本中选择真实依据">知识引用</button>
        <button type="button" className={suggesting ? 'active-tool' : ''} disabled={collaboration?.read_only} onClick={toggleSuggestionMode}>{suggesting ? '修订中' : '修订'}</button>
        <button type="button" disabled={collaboration?.read_only} onClick={() => decideSuggestion(true)} title="接受当前修订">接受</button>
        <button type="button" disabled={collaboration?.read_only} onClick={() => decideSuggestion(false)} title="拒绝当前修订">拒绝</button>
        <button type="button" className="save-button" disabled={saving || collaboration?.read_only} onClick={() => void save('手工保存')}>
          <SaveIcon size={15} />{saving ? '保存中…' : '保存'}
        </button>
      </div>
      <div className="editor-page-wrap">
        <Plate
          editor={editor}
          onChange={({ value }) => {
            valueRef.current = value;
            if (collaboration?.read_only) return;
            onDirtyChange(true);
            window.clearTimeout(saveTimer.current);
            saveTimer.current = window.setTimeout(() => void save(), 1800);
          }}
        >
          {editorReady ? <><FloatingFormatToolbar readOnly={collaboration?.read_only} /><PlateContent className="editor-page" readOnly={collaboration?.read_only} onContextMenu={openAgentContextMenu} placeholder="开始撰写方案…… 输入 / 可打开完整指令菜单；选中文字后右键可扩写、改写或缩写" /></> : <div className="editor-collaboration-loading">正在恢复协同文稿…</div>}
          {collaboration && <CollaborationStatus />}
        </Plate>
      </div>
      <section className="editor-comments" aria-label="协同评论">
        <div className="comment-composer"><input aria-label="评论内容" disabled={collaboration?.role === 'viewer'} value={commentText} onChange={(event) => setCommentText(event.target.value)} placeholder={collaboration?.role === 'viewer' ? '当前角色仅可查看评论' : '选择正文后添加评论'} /><button type="button" disabled={!commentText.trim() || commenting || collaboration?.role === 'viewer'} onClick={() => void createComment()}>{commenting ? '添加中…' : '添加评论'}</button></div>
        {comments.filter((row) => row.status === 'open').map((row) => <article key={row.id}><b>{row.author.name}</b><p>{row.content}</p>{collaboration && ['reviewer', 'publisher', 'owner'].includes(collaboration.role) && <button type="button" onClick={() => void resolveComment(row)}>标记已解决</button>}</article>)}
      </section>
      <div className="editor-status"><span>{saving ? '正在安全保存' : lastSavedHash ? '已保存' : '尚未保存'}</span><span>{collaborationError || (collaboration?.read_only ? '只读协同' : 'Plate 协同与本地恢复已启用')}</span></div>
      {insertDialog && <div className="editor-dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setInsertDialog(null); }}><section className="editor-dialog" role="dialog" aria-modal="true" aria-label={insertDialog === 'link' ? '插入链接' : '插入图片'}><h3>{insertDialog === 'link' ? '插入链接' : '插入图片'}</h3><p>{insertDialog === 'link' ? '输入安全链接地址。已选文字会作为链接文本。' : '输入 HTTPS 图片地址；知识材料中的图片请从右侧依据插入。'}</p><input autoFocus value={insertValue} onChange={(event) => setInsertValue(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') submitInsertDialog(); }} placeholder="https://…" /><div><button type="button" onClick={() => setInsertDialog(null)}>取消</button><button type="button" className="primary" disabled={!insertValue.trim()} onClick={submitInsertDialog}>插入</button></div></section></div>}
      {contextMenu && <div className="agent-context-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !agentEditing) { setContextMenu(null); setAgentEdit(null); } }}><section className="agent-context-menu" style={{ left: contextMenu.x, top: contextMenu.y }} role="dialog" aria-label="妙笔智能修改"><div className="agent-context-title"><b>妙笔修改</b><button type="button" disabled={!!agentEditing} onClick={() => { setContextMenu(null); setAgentEdit(null); }}>×</button></div>{!agentEdit && <div className="agent-action-grid">{([['expand','扩写'],['rewrite','改写'],['shorten','缩写'],['formalize','更正式'],['simplify','更简洁'],['tone','调整语气'],['to_list','转为条目'],['heading','生成小标题'],['add_evidence','补充依据'],['fact_check','核验事实']] as const).map(([key,label]) => <button type="button" key={key} disabled={!!agentEditing} onClick={() => void requestAgentRevision(key)}>{agentEditing === key ? '生成中…' : label}</button>)}</div>}{agentEdit && <div className="agent-edit-preview"><small>原文</small><p>{contextMenu.text}</p><small>建议</small><p>{agentEdit.text}</p><div><button type="button" disabled={!!agentEditing} onClick={() => void decideAgentRevision('reject')}>不采用</button><button type="button" className="primary" disabled={!!agentEditing} onClick={() => void decideAgentRevision('accept')}>{agentEditing === 'accept' ? '应用中…' : '应用建议'}</button></div></div>}{agentEditError && <p className="agent-edit-error">{agentEditError}</p>}</section></div>}
    </div>
  );
}

function FloatingFormatToolbar({ readOnly }: { readOnly: boolean }) {
  const editor = useEditorRef();
  const expanded = useEditorSelector((current) => current.api.isExpanded(), []);
  if (readOnly || !expanded) return null;
  return <div className="floating-format-toolbar" role="toolbar" aria-label="选中文字格式"><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.toggleMark(KEYS.bold)}><b>B</b></button><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.toggleMark(KEYS.italic)}><i>I</i></button><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.toggleMark(KEYS.underline)}><u>U</u></button><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => editor.tf.toggleMark(KEYS.highlight)}>高亮</button><button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => { requestMiaobiEditorCommand('assistant'); }}>妙笔改写</button></div>;
}

function LoaderLabel({ error, onRetry }: { error: string; onRetry: () => void }) {
  return <div className={error ? 'editor-collaboration-error' : 'editor-collaboration-loading'}><span>{error || '正在连接安全协同文稿…'}</span>{error && <button type="button" onClick={onRetry}>重新连接</button>}</div>;
}

function CollaborationStatus() {
  const editor = useEditorRef();
  const connected = usePluginOption(YjsPlugin, '_isConnected');
  const synced = usePluginOption(YjsPlugin, '_isSynced');
  const providers = usePluginOption(YjsPlugin, '_providers');
  return <div className={`collaboration-status ${connected ? 'connected' : ''}`}><span>{connected && synced ? '协同已同步' : connected ? '正在同步' : '协同已断开'}</span><button type="button" onClick={() => connected ? editor.getApi(YjsPlugin).yjs.disconnect('hocuspocus') : editor.getApi(YjsPlugin).yjs.connect('hocuspocus')}>{connected ? '断开' : '重连'}</button><small>{providers.filter((provider) => provider.isConnected).length} 个数据通道在线</small></div>;
}
