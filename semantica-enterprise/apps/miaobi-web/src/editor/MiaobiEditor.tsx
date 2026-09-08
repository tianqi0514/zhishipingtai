import { useEffect, useMemo, useRef, useState } from 'react';
import {
  BlockquotePlugin,
  BoldPlugin,
  H1Plugin,
  H2Plugin,
  H3Plugin,
  HighlightPlugin,
  ItalicPlugin,
  StrikethroughPlugin,
  UnderlinePlugin,
} from '@platejs/basic-nodes/react';
import { CalloutPlugin } from '@platejs/callout/react';
import { CommentPlugin } from '@platejs/comment/react';
import { getCommentKey } from '@platejs/comment';
import { CodeBlockPlugin, CodeLinePlugin } from '@platejs/code-block/react';
import { DatePlugin } from '@platejs/date/react';
import { ColumnItemPlugin, ColumnPlugin } from '@platejs/layout/react';
import { LinkPlugin } from '@platejs/link/react';
import { BaseListPlugin } from '@platejs/list';
import { EquationPlugin, InlineEquationPlugin } from '@platejs/math/react';
import { ImagePlugin } from '@platejs/media/react';
import { MentionPlugin } from '@platejs/mention/react';
import {
  TableCellHeaderPlugin,
  TableCellPlugin,
  TablePlugin,
  TableRowPlugin,
} from '@platejs/table/react';
import { TocPlugin } from '@platejs/toc/react';
import {
  acceptSuggestion,
  BaseSuggestionPlugin,
  getActiveSuggestionDescriptions,
  getSuggestionKey,
  rejectSuggestion,
} from '@platejs/suggestion';
import { SuggestionPlugin } from '@platejs/suggestion/react';
import { YjsPlugin } from '@platejs/yjs/react';
import { KEYS, nanoid, TextApi, type Value } from 'platejs';
import {
  ParagraphPlugin,
  Plate,
  PlateContent,
  PlateElement,
  PlateLeaf,
  type PlateElementProps,
  type PlateLeafProps,
  usePlateEditor,
  useEditorRef,
  usePluginOption,
} from 'platejs/react';
import { api } from '../api';
import type { CollaborationAccess, PlateNode, WritingComment, WritingDocument } from '../types/domain';
import { TrustedBlockKit } from './plugins/trusted-blocks';
import { RemoteCursorOverlay } from './RemoteCursorOverlay';

const Element = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} className={(props.element as Record<string, unknown>).suggestion ? 'block-suggestion' : undefined}>{children}</PlateElement>;
const H1 = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="h1">{children}</PlateElement>;
const H2 = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="h2">{children}</PlateElement>;
const H3 = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="h3">{children}</PlateElement>;
const Quote = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="blockquote">{children}</PlateElement>;
const Strong = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="strong">{children}</PlateLeaf>;
const Emphasis = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="em">{children}</PlateLeaf>;
const Underline = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="u">{children}</PlateLeaf>;
const Strike = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="s">{children}</PlateLeaf>;
const Highlight = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} as="mark">{children}</PlateLeaf>;
const Table = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="table"><tbody>{children}</tbody></PlateElement>;
const TableRow = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="tr">{children}</PlateElement>;
const TableCell = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="td">{children}</PlateElement>;
const TableHeader = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="th">{children}</PlateElement>;
const Link = ({ children, ...props }: PlateElementProps) => <PlateElement {...props} as="a" attributes={{ ...props.attributes, href: String((props.element as Record<string, unknown>).url || '#') }}>{children}</PlateElement>;
const Image = ({ children, ...props }: PlateElementProps) => <PlateElement {...props}><img src={String((props.element as Record<string, unknown>).url || '')} alt="文稿图片" contentEditable={false} />{children}</PlateElement>;
const CommentLeaf = ({ children, ...props }: PlateLeafProps) => <PlateLeaf {...props} className="comment-mark">{children}</PlateLeaf>;
const SuggestionLeaf = ({ children, ...props }: PlateLeafProps) => {
  const data = Object.entries(props.leaf as Record<string, unknown>).find(([key]) => key.startsWith(`${KEYS.suggestion}_`))?.[1] as { type?: string } | undefined;
  return <PlateLeaf {...props} className={`suggestion-mark ${data?.type || 'insert'}`}>{children}</PlateLeaf>;
};

export const EditorKit = [
  ParagraphPlugin.withComponent(Element),
  H1Plugin.withComponent(H1), H2Plugin.withComponent(H2), H3Plugin.withComponent(H3),
  BlockquotePlugin.withComponent(Quote),
  BoldPlugin.withComponent(Strong), ItalicPlugin.withComponent(Emphasis),
  UnderlinePlugin.withComponent(Underline), StrikethroughPlugin.withComponent(Strike),
  HighlightPlugin.withComponent(Highlight),
  LinkPlugin.withComponent(Link),
  TablePlugin.withComponent(Table), TableRowPlugin.withComponent(TableRow),
  TableCellPlugin.withComponent(TableCell), TableCellHeaderPlugin.withComponent(TableHeader),
  CalloutPlugin.withComponent(Element), ColumnPlugin.withComponent(Element), ColumnItemPlugin.withComponent(Element),
  CodeBlockPlugin.withComponent(Element), CodeLinePlugin.withComponent(Element),
  DatePlugin.withComponent(Element), EquationPlugin.withComponent(Element), InlineEquationPlugin.withComponent(Element),
  ImagePlugin.configure({ options: { disableUploadInsert: true }, node: { component: Image } }),
  MentionPlugin.withComponent(Element), TocPlugin.withComponent(Element), BaseListPlugin,
  ...TrustedBlockKit,
];

const EMPTY_VALUE: Value = [
  { id: 'title', type: KEYS.h1, children: [{ text: '应急处置方案' }] },
  { id: 'intro', type: KEYS.p, children: [{ text: '请从左侧目录选择章节，或使用右侧妙笔助手生成有依据的草稿。' }] },
];

type Props = {
  document: WritingDocument;
  onSaved: (document: WritingDocument) => void;
  onDirtyChange: (dirty: boolean) => void;
  onRequestSource: (tab: 'evidence' | 'calculation') => void;
  insertionRequest?: PlateNode | null;
  onInserted?: () => void;
};

export function MiaobiEditor({ document, onSaved, onDirtyChange, onRequestSource, insertionRequest, onInserted }: Props) {
  const initial = (document.current_version?.content?.length ? document.current_version.content : EMPTY_VALUE) as Value;
  const [collaboration, setCollaboration] = useState<CollaborationAccess | null>(null);
  const [collaborationError, setCollaborationError] = useState('');
  const [collaborationAttempt, setCollaborationAttempt] = useState(0);
  const [editorReady, setEditorReady] = useState(false);
  const [comments, setComments] = useState<WritingComment[]>([]);
  const [commentText, setCommentText] = useState('');
  const [commenting, setCommenting] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const cursorColor = useMemo(() => `hsl(${[...document.id].reduce((sum, value) => sum + value.charCodeAt(0), 0) % 360} 66% 46%)`, [document.id]);
  const editor = usePlateEditor({
    plugins: [
      ...EditorKit,
      CommentPlugin.withComponent(CommentLeaf),
      SuggestionPlugin.configure({ options: { currentUserId: collaboration?.user.id || null, isSuggesting: suggesting } }).withComponent(SuggestionLeaf),
      ...(collaboration ? [YjsPlugin.configure({
        options: {
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
      onReady: () => {
        if (!active) return;
        valueRef.current = editor.children as Value;
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
    if (!insertionRequest || collaboration?.read_only) return;
    editor.tf.insertNodes(insertionRequest as Value[number]);
    onInserted?.();
  }, [editor, insertionRequest, onInserted, collaboration?.read_only]);

  const tools = useMemo(() => [
    ['正文', KEYS.p], ['标题1', KEYS.h1], ['标题2', KEYS.h2], ['标题3', KEYS.h3],
  ] as const, []);

  const insertTable = () => editor.tf.insertNodes({
    id: nanoid(), type: KEYS.table, children: [
      { type: KEYS.tr, children: [
        { type: KEYS.th, children: [{ type: KEYS.p, children: [{ text: '项目' }] }] },
        { type: KEYS.th, children: [{ type: KEYS.p, children: [{ text: '内容' }] }] },
      ] },
      { type: KEYS.tr, children: [
        { type: KEYS.td, children: [{ type: KEYS.p, children: [{ text: '待填写' }] }] },
        { type: KEYS.td, children: [{ type: KEYS.p, children: [{ text: '待填写' }] }] },
      ] },
    ],
  });

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
        <button type="button" disabled={collaboration?.read_only} onClick={() => editor.tf.undo()} title="撤销">↶</button>
        <button type="button" disabled={collaboration?.read_only} onClick={() => editor.tf.redo()} title="重做">↷</button>
        <span className="toolbar-divider" />
        {tools.map(([label, type]) => <button type="button" disabled={collaboration?.read_only} key={type} onClick={() => editor.tf.setNodes({ type })}>{label}</button>)}
        <span className="toolbar-divider" />
        <button type="button" disabled={collaboration?.read_only} onClick={() => editor.tf.toggleMark(KEYS.bold)}><b>B</b></button>
        <button type="button" disabled={collaboration?.read_only} onClick={() => editor.tf.toggleMark(KEYS.italic)}><i>I</i></button>
        <button type="button" disabled={collaboration?.read_only} onClick={() => editor.tf.toggleMark(KEYS.underline)}><u>U</u></button>
        <button type="button" disabled={collaboration?.read_only} onClick={() => insertTable()}>表格</button>
        <button type="button" disabled={collaboration?.read_only} onClick={() => onRequestSource('evidence')} title="从锁定知识版本中选择真实依据">知识引用</button>
        <button type="button" disabled={collaboration?.read_only} onClick={() => onRequestSource('calculation')} title="从已执行的确定性计算中选择">测算值</button>
        <button type="button" disabled={collaboration?.read_only} onClick={() => onRequestSource('calculation')} title="从已确认的规则推演结果中选择">推演结论</button>
        <button type="button" className={suggesting ? 'active-tool' : ''} disabled={collaboration?.read_only} onClick={toggleSuggestionMode}>{suggesting ? '修订中' : '修订模式'}</button>
        <button type="button" disabled={collaboration?.read_only} onClick={() => decideSuggestion(true)}>接受修订</button>
        <button type="button" disabled={collaboration?.read_only} onClick={() => decideSuggestion(false)}>拒绝修订</button>
        <button type="button" className="save-button" disabled={saving || collaboration?.read_only} onClick={() => void save('手工保存')}>
          {saving ? '保存中…' : '保存'}
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
          {editorReady ? <PlateContent className="editor-page" readOnly={collaboration?.read_only} placeholder="开始撰写方案……" /> : <div className="editor-collaboration-loading">正在恢复协同文稿…</div>}
          {collaboration && <CollaborationStatus />}
        </Plate>
      </div>
      <section className="editor-comments" aria-label="协同评论">
        <div className="comment-composer"><input aria-label="评论内容" disabled={collaboration?.role === 'viewer'} value={commentText} onChange={(event) => setCommentText(event.target.value)} placeholder={collaboration?.role === 'viewer' ? '当前角色仅可查看评论' : '选择正文后添加评论'} /><button type="button" disabled={!commentText.trim() || commenting || collaboration?.role === 'viewer'} onClick={() => void createComment()}>{commenting ? '添加中…' : '添加评论'}</button></div>
        {comments.filter((row) => row.status === 'open').map((row) => <article key={row.id}><b>{row.author.name}</b><p>{row.content}</p>{collaboration && ['reviewer', 'publisher', 'owner'].includes(collaboration.role) && <button type="button" onClick={() => void resolveComment(row)}>标记已解决</button>}</article>)}
      </section>
      <div className="editor-status"><span>{saving ? '正在安全保存' : lastSavedHash ? '已保存' : '尚未保存'}</span><span>{collaborationError || (collaboration?.read_only ? '只读协同' : 'Plate 协同与本地恢复已启用')}</span></div>
    </div>
  );
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
