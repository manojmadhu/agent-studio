/** Skills: versioned instruction packs that AI steps load for a document domain. */
import { useState } from 'react';
import { api } from '../../api/client';
import { errorMessage, isApiError } from '../../api/errors';
import { qk, useApiMutation, useSkills } from '../../api/hooks';
import type { Skill } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { Dialog } from '../../ui/Dialog';
import { Empty, Loading, PageHead } from '../../ui/bits';
import { toast } from '../../ui/toast';
import { ago } from '../../lib/util';
import { useCrumbs } from '../../app/crumbs';
import { useQueryClient } from '@tanstack/react-query';

function SkillDialog({ s, onClose }: { s: Skill | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(s?.name ?? '');
  const [desc, setDesc] = useState(s?.description ?? '');
  const [ins, setIns] = useState(s?.instructions ?? '');
  const [note, setNote] = useState('');
  const create = useApiMutation(() => api.createSkill({ name: name.trim(), description: desc, instructions: ins }), [qk.skills], (k) => toast(`${k.name} created`));
  const publish = useApiMutation(() => api.publishSkillVersion(s!.id, ins, note), [qk.skills], (k) => toast(`${k.name} v${k.version} published`));
  return (
    <Dialog
      title={s ? s.name : 'New skill'}
      desc={s ? `Version ${s.version} · used by ${s.usage_count ?? 0} steps. Publishing creates version ${s.version + 1}.` : 'Instructions are loaded by AI steps that select this skill.'}
      okLabel={s ? `Publish v${s.version + 1}` : 'Create skill'}
      wide
      onClose={onClose}
      footer={
        s ? (
          <button
            className="btn danger"
            style={{ marginRight: 'auto' }}
            onClick={async () => {
              try {
                await api.deleteSkill(s.id);
                await qc.invalidateQueries({ queryKey: qk.skills });
                toast(`${s.name} deleted`);
                onClose();
              } catch (e) {
                toast(isApiError(e) && e.status === 409 ? `${s.name} is used by ${e.problem.usages?.length ?? 0} steps and cannot be deleted` : errorMessage(e), true);
              }
            }}
          >
            Delete
          </button>
        ) : undefined
      }
      onOk={async () => {
        if (!s && (!name.trim() || !ins.trim())) {
          toast('Name and instructions are required', true);
          return false;
        }
        return (s ? publish.mutateAsync(undefined) : create.mutateAsync(undefined)).then(
          () => undefined,
          () => false,
        );
      }}
    >
      {!s && (
        <label className="field">
          <span className="lab">
            Name<span className="req">*</span>
          </span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
      )}
      {!s && (
        <label className="field">
          <span className="lab">Description</span>
          <input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} />
        </label>
      )}
      <label className="field">
        <span className="lab">Instructions</span>
        <textarea className="textarea" rows={8} value={ins} onChange={(e) => setIns(e.target.value)} />
      </label>
      {s && (
        <>
          <label className="field">
            <span className="lab">Release note</span>
            <input className="input" value={note} placeholder="What changed" onChange={(e) => setNote(e.target.value)} />
          </label>
          <h3 className="section-h">History</h3>
          {(s.versions ?? []).map((v) => (
            <div className="toolrow" key={v.version}>
              <span className="mono" style={{ width: 36 }}>
                v{v.version}
              </span>
              <span className="grow">{v.release_note}</span>
              <span className="muted" style={{ fontSize: 12.5 }}>
                {v.published_by} · {ago(v.published_at)}
              </span>
            </div>
          ))}
        </>
      )}
    </Dialog>
  );
}

export function SkillsPage() {
  useCrumbs([['Skills', '/skills']]);
  const skills = useSkills();
  const [dlg, setDlg] = useState<Skill | 'new' | null>(null);
  return (
    <div className="page">
      <PageHead
        title="Skills"
        desc="Versioned instruction packs that AI steps load for a document domain."
        actions={
          <button className="btn primary" onClick={() => setDlg('new')}>
            <Icon name="add" />
            New skill
          </button>
        }
      />
      <div className="card">
        <div className="tablewrap">
          {skills.isLoading ? (
            <Loading />
          ) : (
            <table className="table stack">
              <thead>
                <tr>
                  <th>Skill</th>
                  <th>Version</th>
                  <th>Updated</th>
                  <th className="n">Used by</th>
                </tr>
              </thead>
              <tbody>
                {skills.data?.length ? (
                  skills.data.map((k) => (
                    <tr key={k.id} className="click" onClick={() => setDlg(k)}>
                      <td>
                        <div className="row" style={{ gap: 12 }}>
                          <span className="tile">
                            <Icon name="book" />
                          </span>
                          <div className="cellt">
                            <b>{k.name}</b>
                            <small>{k.description}</small>
                          </div>
                        </div>
                      </td>
                      <td className="mono">v{k.version}</td>
                      <td className="sub">{ago(k.versions?.[0]?.published_at)}</td>
                      <td className="n">{k.usage_count ?? 0} steps</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4}>
                      <Empty icon="book" title="No skills" />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {dlg && <SkillDialog s={dlg === 'new' ? null : dlg} onClose={() => setDlg(null)} />}
    </div>
  );
}
