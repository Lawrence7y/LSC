/**
 * en-US 词典组装器：按模块分片（en.part.*.ts）合并。
 * 分片由各模块翻译任务独立产出，避免并发编辑冲突；
 * 若某分片不存在或为空，用空对象占位（词典为按需渐进式补充）。
 */
import { enPartUi } from './en.part.ui'
import { enPartRoom } from './en.part.room'
import { enPartClips } from './en.part.clips'
import { enPartWorkbench } from './en.part.workbench'
import { enPartSettings } from './en.part.settings'
import { enPartMisc } from './en.part.misc'
import { enPartLogic } from './en.part.logic'
import { enPartPlatform } from './en.part.platform'

export const enDict: Record<string, string> = {
  ...enPartUi,
  ...enPartRoom,
  ...enPartClips,
  ...enPartWorkbench,
  ...enPartSettings,
  ...enPartMisc,
  ...enPartLogic,
  ...enPartPlatform,
}
