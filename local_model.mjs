/** Local OpenAI-compatible adapter for a GGUF model. It does not run any tools. */
import {createServer} from 'node:http';
import {randomUUID} from 'node:crypto';
import {getLlama, LlamaChatSession} from './.tooling/node_modules/node-llama-cpp/dist/index.js';

const [, , modelPath, port = '11455'] = process.argv;
if (!modelPath) throw new Error('Pass the GGUF model path.');
const llama = await getLlama({gpu: process.env.REHEARSAL_GPU === 'vulkan' ? 'vulkan' : false, build: 'never'});
const model = await llama.loadModel({modelPath});
const context = await model.createContext({contextSize: 8192, threads: 8, sequences: 1});

function dialogue(messages) {
    return messages.map(m => {
        const calls = m.tool_calls?.map(call => call.function.name).join(', ');
        return m.role + (m.name ? ' ' + m.name : '') + ': ' +
            (calls ? 'Called ' + calls + '\n' : '') + (m.content ?? '');
    }).join('\n\n');
}

async function complete(body) {
    const tools = (body.tools ?? []).map(t => t.function);
    const forced = body.tool_choice?.function?.name;
    const required = forced ? tools.find(t => t.name === forced) :
        (body.tool_choice === 'required' && tools.length === 1 ? tools[0] : null);
    console.log("Model request",required?.name ?? (tools.length ? "agent choice" : "text"));
    const session = new LlamaChatSession({contextSequence: context.getSequence(), autoDisposeSequence: true});
    try {
        let grammar, prompt;
        if (required) {
            grammar = await llama.createGrammarForJsonSchema(required.parameters);
            prompt = dialogue(body.messages) + '\n\nReturn only JSON arguments for the required function ' +
                required.name + '. Its parameter schema is ' + JSON.stringify(required.parameters);
        } else if (tools.length) {
            // The model selects the tool from the whole surface, or chooses to stop.
            const names = tools.map(t => t.name);
            if (body.tool_choice !== 'required') names.push('final');
            grammar = await llama.createGrammarForJsonSchema({
                type:'object', properties:{
                    action:{enum:names}, message:{type:'string', maxLength:body.tool_choice === 'required' ? 0 : 200},
                }, required:['action','message'], additionalProperties:false,
            });
            prompt = dialogue(body.messages) +
                '\n\nAvailable tools: ' + JSON.stringify(tools.map(t => [t.name,t.description])) +
                '\nChoose exactly one action. For an available tool, give its exact name and an empty message. ' +
                'Use "final" and summarize the evidence only after a tool returned the verdict.' +
                '\nLatest instruction: ' + (body.messages.at(-1)?.content ?? '');
        } else {
            if (body.response_format?.type === 'json_object') grammar = await llama.getGrammarFor('json');
            prompt = dialogue(body.messages);
        }
        let text = '', completed;
        const abort = new AbortController();
        const response = await session.prompt(prompt, {
            signal: abort.signal, stopOnAbortSignal: true,
            onTextChunk(chunk) {
                text += chunk;
                if (text.length % 200 < chunk.length) console.log("Generated chars", text.length);
                if (grammar && text.trimEnd().endsWith('}')) {
                    try { completed = JSON.parse(text); abort.abort(); } catch {}
                }
            },
            maxTokens: Math.min(body.max_tokens ?? 650, required ? 650 : 180),
            temperature:0, ...(grammar ? {grammar} : {}),
        });
        if (required) {
            const args = completed ?? JSON.parse(response);
            if (process.env.REHEARSAL_MODEL_TRACE === '1') console.log('Generated plan', JSON.stringify(args));
            return toolMessage(required.name, args);
        }
        if (tools.length) {
            const choice = completed ?? JSON.parse(response);
            return choice.action === 'final' ? {role:'assistant',content:choice.message} :
                toolMessage(choice.action, {});
        }
        return {role:'assistant',content:response};
    } finally {
        session.dispose();
    }
}

function toolMessage(name, args) {
    return {role:'assistant',content:null,tool_calls:[{
        type:'function',id:'call_' + randomUUID(),function:{name,arguments:JSON.stringify(args)},
    }]};
}

let queue = Promise.resolve();
createServer((req, res) => {
    const write = (code, packet) => {
        res.writeHead(code,{'Content-Type':'application/json'});
        res.end(JSON.stringify(packet));
    };
    if (req.method === 'GET' && req.url === '/health') return write(200,{status:'ok'});
    if (req.method !== 'POST' || req.url !== '/v1/chat/completions') return write(404,{error:'Unknown route'});
    let raw = '';
    req.setEncoding('utf8');
    req.on('data', part => { raw += part; if (raw.length > 2_000_000) req.destroy(); });
    req.on('end', () => {
        queue = queue.then(async () => {
            try {
                const input = JSON.parse(raw);
                const message = await complete(input);
                console.log('Model response',message.tool_calls?.[0]?.function.name ?? 'text');
                write(200,{id:randomUUID(),object:'chat.completion',model:input.model,
                    choices:[{index:0,message,finish_reason:message.tool_calls ? 'tool_calls' : 'stop'}]});
            } catch (e) {
                console.error(e);
                write(500,{error:{message:String(e)}});
            }
        });
    });
}).listen(Number(port),'127.0.0.1', () => console.log('Local model listening on',port));
