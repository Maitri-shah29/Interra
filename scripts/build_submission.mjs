// Validate the canonical 8-slide deck. Alternate presentation generation is prohibited.
import {spawnSync} from 'node:child_process';
const result=spawnSync(process.env.RUNTIME_PYTHON || 'python',['scripts/check_submission.py'],{stdio:'inherit'});
if(result.error) throw result.error;
process.exit(result.status ?? 1);
