-- | Trimmed, newer-pinned fork of the dwarf-adversary ChainSync connection
-- layer, ported to the ouroboros-network 1.2.0.0 / cardano-diffusion 1.1.0.0
-- stack used by cardano-node 11.1.2. Only the client capture path
-- (runChainSyncApplication) and the single-header serve path
-- (runChainSyncServer) that the opcert peer needs are kept.
--
-- Port deltas vs the 0.x stack:
--   * node-to-node versioning/handshake moved from Ouroboros.Network.NodeToNode
--     to Cardano.Network.NodeToNode (cardano-diffusion).
--   * NodeToNodeVersionData gained a perasSupport field (PerasUnsupported for
--     NodeToNodeV_14, which predates Peras).
--   * withServerNode + ErrorPolicy were removed; the server now binds via
--     Ouroboros.Network.Server.Simple.with with a HandshakeArguments record.
--   * connectToNode socket-options arg became a (fd -> m ()) configure hook;
--     mempty still satisfies it.
module DwarfAdversary.ChainSync.Connection
    ( runChainSyncApplication
    , runChainSyncServer
    , blockFetchResponder
    , onDemandBlockFetchResponder
    , plainBlockFetchCodec
    , plainTxSubmissionCodec
    , HeaderHash
    , ChainSyncApplication
    )
where

import DwarfAdversary.ChainSync.Codec
    ( Block, GenTx, GenTxId, Header, Point, Tip, codecChainSync
    , decBlock, decBlockPoint, decTx, decTxId
    , encBlock, encBlockPoint, encTx, encTxId
    )
import Codec.Serialise (DeserialiseFailure)
import Control.Concurrent (threadDelay)
import Control.Exception (SomeException, try)
import Control.DeepSeq (NFData)
import Control.Concurrent.Class.MonadSTM.Strict
    ( MonadSTM (atomically), StrictTVar, newTVarIO, readTVar, writeTVar )
import Ouroboros.Consensus.Block (headerPoint)
import Ouroboros.Network.Mock.Chain (Chain)
import Ouroboros.Network.Mock.Chain qualified as Chain
import Ouroboros.Network.Protocol.BlockFetch.Client
    ( BlockFetchClient (BlockFetchClient)
    , BlockFetchReceiver (BlockFetchReceiver, handleBatchDone, handleBlock)
    , BlockFetchRequest (SendMsgClientDone, SendMsgRequestRange)
    , BlockFetchResponse (BlockFetchResponse, handleNoBlocks, handleStartBatch)
    , blockFetchClientPeer
    )
import Ouroboros.Network.Protocol.BlockFetch.Type (ChainRange (ChainRange))
import Control.Monad.Class.MonadAsync (wait)
import Control.Tracer (nullTracer)
import Data.ByteString.Lazy (LazyByteString)
import Data.List.NonEmpty qualified as NE
import Data.Void (Void)
import Data.Word (Word16)
import Network.Mux qualified as Mx
import Network.Socket
    ( AddrInfo (..), AddrInfoFlag (AI_PASSIVE), PortNumber
    , SocketType (Stream), defaultHints, getAddrInfo
    )
import Network.TypedProtocol.Codec (Codec)
import Network.TypedProtocol.Core (N (Z))
import Ouroboros.Network.Block qualified as Network
import Ouroboros.Network.IOManager (withIOManager)
import Ouroboros.Network.Magic (NetworkMagic (..))
import Ouroboros.Network.Mux
    ( MiniProtocol (..), MiniProtocolLimits (..), MiniProtocolNum (MiniProtocolNum)
    , OuroborosApplication (..), OuroborosApplicationWithMinimalCtx
    , RunMiniProtocol (InitiatorProtocolOnly, ResponderProtocolOnly)
    , StartOnDemandOrEagerly (StartEagerly, StartOnDemand)
    , mkMiniProtocolCbFromPeer, mkMiniProtocolCbFromPeerPipelined
    )
import Cardano.Network.NodeToNode
    ( DiffusionMode (InitiatorAndResponderDiffusionMode, InitiatorOnlyDiffusionMode)
    , NodeToNodeVersion (NodeToNodeV_14)
    , NodeToNodeVersionData (..)
    , nodeToNodeHandshakeCodec
    , nodeToNodeVersionDataCodec
    , simpleSingletonVersions
    )
import Ouroboros.Network.PeerSelection.PeerSharing (PeerSharing (PeerSharingDisabled))
import Ouroboros.Network.PerasSupport (PerasSupport (PerasUnsupported))
import Ouroboros.Network.Protocol.ChainSync.Client (ChainSyncClient (..))
import Ouroboros.Network.Protocol.ChainSync.Client qualified as ChainSync
import Ouroboros.Network.Protocol.ChainSync.Server (ChainSyncServer, chainSyncServerPeer)
import Ouroboros.Network.Protocol.ChainSync.Type (ChainSync)
import Ouroboros.Network.Protocol.BlockFetch.Codec (codecBlockFetch)
import Ouroboros.Network.Protocol.BlockFetch.Server
    ( BlockFetchBlockSender (SendMsgNoBlocks, SendMsgStartBatch)
    , BlockFetchSendBlocks (SendMsgBatchDone, SendMsgBlock)
    , BlockFetchServer (BlockFetchServer)
    , blockFetchServerPeer
    )
import Ouroboros.Network.Protocol.BlockFetch.Type (BlockFetch)
import Ouroboros.Network.Protocol.KeepAlive.Client
    ( KeepAliveClient (KeepAliveClient), KeepAliveClientSt (SendMsgKeepAlive)
    , keepAliveClientPeer
    )
import Ouroboros.Network.Protocol.KeepAlive.Codec (codecKeepAlive_v2)
import Ouroboros.Network.Protocol.KeepAlive.Server
    ( KeepAliveServer (KeepAliveServer, recvMsgDone, recvMsgKeepAlive), keepAliveServerPeer )
import Ouroboros.Network.Protocol.KeepAlive.Type (Cookie (Cookie))
import Ouroboros.Network.Protocol.TxSubmission2.Codec (codecTxSubmission2)
import Ouroboros.Network.Protocol.TxSubmission2.Server
    ( ServerStIdle (SendMsgRequestTxIdsBlocking)
    , TxSubmissionServerPipelined (TxSubmissionServerPipelined)
    , txSubmissionServerPeerPipelined
    )
import Ouroboros.Network.Protocol.TxSubmission2.Type
    ( NumTxIdsToAck (NumTxIdsToAck), NumTxIdsToReq (NumTxIdsToReq), TxSubmission2 )
import Ouroboros.Network.Protocol.Handshake (HandshakeArguments (..))
import Ouroboros.Network.Protocol.Handshake.Codec (noTimeLimitsHandshake)
import Ouroboros.Network.Protocol.Handshake.Version
    ( Acceptable (acceptableVersion), Queryable (queryVersion) )
import Ouroboros.Network.Server.Simple qualified as Server.Simple
import Ouroboros.Network.Snocket (makeSocketBearer, socketSnocket)
import Ouroboros.Network.Socket
    ( ConnectToArgs (..), HandshakeCallbacks (..), SomeResponderApplication (..)
    , connectToNode, nullNetworkConnectTracers
    )

-- | The application type for a chain sync client
type ChainSyncApplication = ChainSyncClient Header Point Tip IO ()

-- | The header hash type used in the chain sync connection
type HeaderHash = Network.HeaderHash Block

-- | Node-to-node version data for NodeToNodeV_14 (pre-Peras, peer-sharing off).
ntnVersionData :: NetworkMagic -> DiffusionMode -> NodeToNodeVersionData
ntnVersionData magic dmode =
    NodeToNodeVersionData
        { networkMagic = magic
        , diffusionMode = dmode
        , peerSharing = PeerSharingDisabled
        , query = False
        , perasSupport = PerasUnsupported
        }

-- | Connect to a node-to-node chain sync server and run the given application
-- (initiator role -- retained for header capture).
runChainSyncApplication
    :: NetworkMagic
    -> String
    -> PortNumber
    -> (NodeToNodeVersionData -> ChainSyncApplication)
    -> IO (Either SomeException (Either () Void))
runChainSyncApplication magic peerName peerPort application = withIOManager $ \iocp -> do
    AddrInfo{addrAddress} <- resolve peerName peerPort
    connectToNode
        (socketSnocket iocp)
        makeSocketBearer
        ConnectToArgs
            { ctaHandshakeCodec = nodeToNodeHandshakeCodec
            , ctaHandshakeTimeLimits = noTimeLimitsHandshake
            , ctaVersionDataCodec = nodeToNodeVersionDataCodec
            , ctaConnectTracers = nullNetworkConnectTracers
            , ctaHandshakeCallbacks =
                HandshakeCallbacks { acceptCb = acceptableVersion, queryCb = queryVersion }
            }
        mempty
        ( simpleSingletonVersions
            NodeToNodeV_14
            (ntnVersionData magic InitiatorOnlyDiffusionMode)
            (chainSyncToOuroboros . application)
        )
        Nothing
        addrAddress

-- | Bind and listen as a chain-sync server (responder role) using the new
-- Server.Simple API. @onAccept@ fires per inbound socket (via the configure
-- hook). Serves the given chain-sync @server@ under @codec@ on mini-protocol 2,
-- plus block-fetch (#3), keep-alive (#8) and tx-submission (#4) responders so
-- the node keeps the bearer open. Blocks forever.
runChainSyncServer
    :: NetworkMagic
    -> PortNumber
    -> (String -> IO ())
    -> Codec (ChainSync Header Point Tip) DeserialiseFailure IO LazyByteString
    -> ChainSyncServer Header Point Tip IO ()
    -> Codec (BlockFetch Block (Network.Point Block)) DeserialiseFailure IO LazyByteString
    -> BlockFetchServer Block (Network.Point Block) IO ()
    -> IO Void
runChainSyncServer magic port onAccept codec server bfCodec bfServer = withIOManager $ \iocp -> do
    AddrInfo{addrAddress} <- resolveBind port
    Server.Simple.with
        (socketSnocket iocp)
        nullTracer
        Mx.nullTracers
        makeSocketBearer
        (\_fd peerAddr -> onAccept (show peerAddr))
        addrAddress
        HandshakeArguments
            { haHandshakeTracer = nullTracer
            , haBearerTracer = nullTracer
            , haHandshakeCodec = nodeToNodeHandshakeCodec
            , haVersionDataCodec = nodeToNodeVersionDataCodec
            , haAcceptVersion = acceptableVersion
            , haQueryVersion = queryVersion
            , haTimeLimits = noTimeLimitsHandshake
            }
        ( simpleSingletonVersions
            NodeToNodeV_14
            (ntnVersionData magic InitiatorAndResponderDiffusionMode)
            (\_ -> SomeResponderApplication (chainSyncToResponder codec server bfCodec bfServer))
        )
        (\_addr serverAsync -> wait serverAsync)

resolve :: String -> PortNumber -> IO AddrInfo
resolve peerName peerPort = do
    let hints = defaultHints { addrFlags = [AI_PASSIVE], addrSocketType = Stream }
    NE.head <$> getAddrInfo (Just hints) (Just peerName) (Just $ show peerPort)

resolveBind :: PortNumber -> IO AddrInfo
resolveBind port = do
    let hints = defaultHints { addrFlags = [AI_PASSIVE], addrSocketType = Stream }
    NE.head <$> getAddrInfo (Just hints) (Just "0.0.0.0") (Just $ show port)

maximumMiniProtocolLimits :: MiniProtocolLimits
maximumMiniProtocolLimits = MiniProtocolLimits { maximumIngressQueue = maxBound }

chainSyncToOuroboros
    :: ChainSyncApplication
    -> OuroborosApplicationWithMinimalCtx Mx.InitiatorMode addr LazyByteString IO () Void
chainSyncToOuroboros chainSyncApp =
    OuroborosApplication
        [ MiniProtocol
            { miniProtocolNum = MiniProtocolNum 2
            , miniProtocolStart = StartOnDemand
            , miniProtocolLimits = maximumMiniProtocolLimits
            , miniProtocolRun =
                InitiatorProtocolOnly $ mkMiniProtocolCbFromPeer $ \_ctx ->
                    ( nullTracer, codecChainSync, ChainSync.chainSyncClientPeer chainSyncApp )
            }
        , MiniProtocol
            { miniProtocolNum = MiniProtocolNum 8
            , miniProtocolStart = StartEagerly
            , miniProtocolLimits = maximumMiniProtocolLimits
            , miniProtocolRun =
                InitiatorProtocolOnly $ mkMiniProtocolCbFromPeer $ \_ctx ->
                    ( nullTracer, codecKeepAlive_v2, keepAliveClientPeer keepAliveInitiator )
            }
        ]

keepAliveInitiator :: KeepAliveClient IO ()
keepAliveInitiator = KeepAliveClient (go 0)
  where
    go :: Word16 -> IO (KeepAliveClientSt IO ())
    go c = pure (SendMsgKeepAlive (Cookie c) (threadDelay 20000000 >> go (c + 1)))

chainSyncToResponder
    :: Codec (ChainSync Header Point Tip) DeserialiseFailure IO LazyByteString
    -> ChainSyncServer Header Point Tip IO ()
    -> Codec (BlockFetch Block (Network.Point Block)) DeserialiseFailure IO LazyByteString
    -> BlockFetchServer Block (Network.Point Block) IO ()
    -> OuroborosApplicationWithMinimalCtx Mx.ResponderMode addr LazyByteString IO Void ()
chainSyncToResponder codec server bfCodec bfServer =
    OuroborosApplication
        [ responder 2 $ mkMiniProtocolCbFromPeer
            (const (nullTracer, codec, chainSyncServerPeer server))
        , responder 3 $ mkMiniProtocolCbFromPeer
            (const (nullTracer, bfCodec, blockFetchServerPeer bfServer))
        , responder 8 $ mkMiniProtocolCbFromPeer
            (const (nullTracer, codecKeepAlive_v2, keepAliveServerPeer keepAliveResponder))
        , responder 4 $ mkMiniProtocolCbFromPeerPipelined
            (const (nullTracer, codecTxSubmission2 encTxId decTxId encTx decTx
                   , txSubmissionServerPeerPipelined txSubmissionResponder))
        ]
  where
    responder num cb =
        MiniProtocol
            { miniProtocolNum = MiniProtocolNum num
            , miniProtocolStart = StartOnDemand
            , miniProtocolLimits = maximumMiniProtocolLimits
            , miniProtocolRun = ResponderProtocolOnly cb
            }

keepAliveResponder :: KeepAliveServer IO ()
keepAliveResponder =
    KeepAliveServer { recvMsgKeepAlive = pure keepAliveResponder, recvMsgDone = pure () }

blockFetchResponder :: BlockFetchServer Block (Network.Point Block) IO ()
blockFetchResponder =
    BlockFetchServer (\_range -> pure (SendMsgNoBlocks (pure blockFetchResponder))) ()

plainBlockFetchCodec
    :: Codec (BlockFetch Block (Network.Point Block)) DeserialiseFailure IO LazyByteString
plainBlockFetchCodec = codecBlockFetch encBlock decBlock encBlockPoint decBlockPoint

plainTxSubmissionCodec
    :: Codec (TxSubmission2 (GenTxId Block) (GenTx Block)) DeserialiseFailure IO LazyByteString
plainTxSubmissionCodec = codecTxSubmission2 encTxId decTxId encTx decTx

txSubmissionResponder :: TxSubmissionServerPipelined (GenTxId Block) (GenTx Block) IO ()
txSubmissionResponder = TxSubmissionServerPipelined (pure (idle 0))
  where
    idle :: Word16 -> ServerStIdle Z (GenTxId Block) (GenTx Block) IO ()
    idle ack =
        SendMsgRequestTxIdsBlocking
            (NumTxIdsToAck ack)
            (NumTxIdsToReq 1)
            (pure ())
            (\txids -> pure (idle (fromIntegral (NE.length txids))))


-- | Initiator-only app running our block-fetch client on mini-protocol #3.
blockFetchToOuroboros
    :: NFData a => BlockFetchClient Block (Network.Point Block) IO a
    -> OuroborosApplicationWithMinimalCtx Mx.InitiatorMode addr LazyByteString IO a Void
blockFetchToOuroboros app =
    OuroborosApplication
        [ MiniProtocol
            { miniProtocolNum = MiniProtocolNum 3
            , miniProtocolStart = StartOnDemand
            , miniProtocolLimits = maximumMiniProtocolLimits
            , miniProtocolRun =
                InitiatorProtocolOnly $ mkMiniProtocolCbFromPeer $ \_ctx ->
                    ( nullTracer
                    , codecBlockFetch encBlock decBlock encBlockPoint decBlockPoint
                    , blockFetchClientPeer app
                    )
            }
        ]

runBlockFetchApplication
    :: NFData a
    => NetworkMagic
    -> String
    -> PortNumber
    -> (NodeToNodeVersionData -> BlockFetchClient Block (Network.Point Block) IO a)
    -> IO (Either SomeException (Either a Void))
runBlockFetchApplication magic peerName peerPort application = withIOManager $ \iocp -> do
    AddrInfo{addrAddress} <- resolve peerName peerPort
    connectToNode
        (socketSnocket iocp)
        makeSocketBearer
        ConnectToArgs
            { ctaHandshakeCodec = nodeToNodeHandshakeCodec
            , ctaHandshakeTimeLimits = noTimeLimitsHandshake
            , ctaVersionDataCodec = nodeToNodeVersionDataCodec
            , ctaConnectTracers = nullNetworkConnectTracers
            , ctaHandshakeCallbacks =
                HandshakeCallbacks { acceptCb = acceptableVersion, queryCb = queryVersion }
            }
        mempty
        ( simpleSingletonVersions
            NodeToNodeV_14
            (ntnVersionData magic InitiatorOnlyDiffusionMode)
            (blockFetchToOuroboros . application)
        )
        Nothing
        addrAddress

-- | Fetch one block body at @point@ from upstream via block-fetch.
fetchBlock
    :: NetworkMagic -> String -> PortNumber -> Network.Point Block
    -> IO (Either SomeException (Maybe Block))
fetchBlock magic host port point = do
    resultVar <- newTVarIO Nothing
    let done = BlockFetchClient (pure (SendMsgClientDone ()))
        receiver =
            BlockFetchReceiver
                { handleBlock = \blk -> do
                    atomically (writeTVar resultVar (Just blk))
                    pure receiver
                , handleBatchDone = pure ()
                }
        response =
            BlockFetchResponse
                { handleStartBatch = pure receiver
                , handleNoBlocks = pure ()
                }
        client =
            BlockFetchClient
                (pure (SendMsgRequestRange (ChainRange point point) response done))
    res <- try $ runBlockFetchApplication magic host port (const client)
    case res of
        Left e -> pure (Left e)
        Right _ -> Right <$> atomically (readTVar resultVar)

-- | Block-fetch responder for the advancing chain: reads the shared growing
-- chain and fetches each requested body from upstream on demand, serving them in
-- order so the downstream node can validate + ADOPT the (re-signed) chain.
onDemandBlockFetchResponder
    :: (String -> IO ())
    -> (Block -> IO ())
    -> NetworkMagic
    -> (String, Int)
    -> StrictTVar IO (Chain Header)
    -> BlockFetchServer Block (Network.Point Block) IO ()
onDemandBlockFetchResponder log_ onServe magic (host, port) chainVar = server
  where
    server = BlockFetchServer handleRange ()
    handleRange (ChainRange lo hi) = do
        hdrs <- Chain.toOldestFirst <$> atomically (readTVar chainVar)
        let pts = map (Network.castPoint . headerPoint) hdrs
            inRange = takeWhile (<= hi) (dropWhile (< lo) pts)
        log_ ("block-fetch(on-demand): MsgRequestRange -> " <> show (length inRange) <> " points")
        case inRange of
            [] -> pure (SendMsgNoBlocks (pure server))
            _ -> pure (SendMsgStartBatch (sendBlocks inRange))
    sendBlocks [] = pure (SendMsgBatchDone (pure server))
    sendBlocks (pt : pts) = do
        r <- fetchBlock magic host (fromIntegral port) pt
        case r of
            Right (Just b) -> do
                onServe b
                pure (SendMsgBlock b (sendBlocks pts))
            _ -> do
                log_ "block-fetch(on-demand): body miss; skipping"
                sendBlocks pts
