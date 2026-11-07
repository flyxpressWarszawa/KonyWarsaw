## Render deployment: stuck build / optional COMBO SLM

The default worker build installs only the core runtime requirements from `requirements.txt`. The optional CPU inference dependency in `requirements-combo-slm.txt` is intentionally not pulled during every worker deployment; its large native wheel can make a routine worker build slow or fail before the worker starts. The queue worker and all non-SLM lanes do not require `llama-cpp-python`.

To enable the optional local COMBO SLM, install `requirements-combo-slm.txt` explicitly in a separately planned deployment/build after confirming the Render environment's architecture and available memory. Do not enable `MSW_COMBO_SLM_ENABLED` unless that optional dependency is installed and the model has sufficient memory/disk. If it is absent, the optional COMBO child should report inference as unverified; the main worker should still start and process its normal queue.

The Blueprint build command prints phase markers before dependency installation and bytecode compilation. If a deploy remains at **Building** with no log output, the build has not yet reached those commands; inspect the deployment's Events and Render status rather than repeatedly starting concurrent deploys. Do not clear or recreate the persistent disk to troubleshoot a build: source builds do not need to modify the worker's `/var/data` state.
