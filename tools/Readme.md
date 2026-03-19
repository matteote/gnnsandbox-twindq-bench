# Network Agent MCP Tools

Follow the instructions below to deploy and run the network agent MCP tools.

## Deploy the Network Agent Tools to GCP

The agent backend, dashboard and tools are deployed by running the following command.

```
install.sh -n
```

## Running the Network Agent tools locally from VSCode

To run the network agent tools on your local machine, you must first install its python dependencies, i.e.

```
pip install -r networkagents/engineer/requirements.txt
```

To run the network agent tools in VSCode you can setup a __launch.json__ file as below. 

```
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Python Debugger: Current File",
            "type": "debugpy",
            "request": "launch",
            "program": "${file}",
            "env": {
                "BASEDIR": "<YOUR LOCAL DIR>/NetworkAgent/operator/src",
                "GOOGLE_PROJECT": "<YOUR PROJECT>",
                "GOOGLE_REGION": "<YOUR REGION>",
                "GOOGLE_ZONE": "<YOUR ZONE>",
                "ROOT_DIR" : "networkagent/src/",
                "NETWORK_AGENT_FILE": "./networkagent.json"
            }
        }
    ]
}
```


## Test the server

Install node and npx on your local machine and run the MCP inspector as follows:

```
npx @modelcontextprotocol/inspector
```

Enter the url for the mcp server and test tools. 